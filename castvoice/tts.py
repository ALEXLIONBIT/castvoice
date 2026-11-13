"""Sintesi vocale: voci locali, voci online e voce clonata.

Id delle voci:  "espeak:<voce>"  |  "edge:<ShortName>"  |  "clone:<slug profilo>"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .library import VoiceStore

log = logging.getLogger("castvoice")

ESPEAK_VOICES = [
    ("it", "Italiano (robot classico)"),
    ("it+m3", "Italiano – uomo"),
    ("it+m7", "Italiano – uomo grave"),
    ("it+f2", "Italiano – donna"),
    ("it+f4", "Italiano – donna acuta"),
    ("it+whisper", "Italiano – sussurro"),
    ("it+croak", "Italiano – rospo"),
]

# Se non c'è rete per scaricare l'elenco, queste restano disponibili.
EDGE_FALLBACK = [
    ("it-IT-IsabellaNeural", "Isabella", "Female", "it-IT"),
    ("it-IT-ElsaNeural", "Elsa", "Female", "it-IT"),
    ("it-IT-DiegoNeural", "Diego", "Male", "it-IT"),
    ("it-IT-GiuseppeMultilingualNeural", "Giuseppe", "Male", "it-IT"),
]
EDGE_LANGS = ("it", "en", "es", "fr", "de", "pt")


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    engine: str  # espeak | edge | clone

    @property
    def online(self) -> bool:
        return self.engine == "edge"


class ClonePending(RuntimeError):
    """Il motore di clonazione non è pronto (non installato o licenza non accettata)."""


def edge_voices() -> list[tuple[str, str, str, str]]:
    """[(ShortName, nome, genere, locale)] – prima l'italiano."""
    try:
        import edge_tts

        raw = asyncio.run(edge_tts.list_voices())
    except Exception as exc:  # noqa: BLE001 - offline: usa l'elenco minimo
        log.info("edge-tts offline (%s), uso l'elenco minimo", exc)
        return list(EDGE_FALLBACK)
    out = []
    for v in raw:
        if v["Locale"].split("-")[0] in EDGE_LANGS:
            name = v["ShortName"].split("-")[-1].replace("Neural", "").replace("Multilingual", " (multilingua)")
            out.append((v["ShortName"], name, v["Gender"], v["Locale"]))
    out.sort(key=lambda t: (not t[3].startswith("it"), t[3], t[1]))
    return out or list(EDGE_FALLBACK)


def list_voices(store: VoiceStore, online: bool = True) -> list[Voice]:
    voices = [Voice(f"espeak:{v}", f"{label} · locale", "espeak") for v, label in ESPEAK_VOICES]
    for p in store.profiles():
        voices.insert(0, Voice(f"clone:{p.slug}", f"La mia voce – {p.name} · clonata", "clone"))
    if online:
        for short, name, gender, locale in edge_voices():
            sesso = "donna" if gender == "Female" else "uomo"
            voices.append(Voice(f"edge:{short}", f"{name} ({sesso}, {locale}) · online", "edge"))
    return voices


# --------------------------------------------------------------------------
# Clonazione (XTTS v2) in un processo separato, fuori dal progetto
# --------------------------------------------------------------------------
WORKER = Path(__file__).with_name("clone_worker.py")
CLONE_INSTALL_CMD = (
    f'uv venv --python 3.12 "{paths.CLONE_ENV}" && '
    f'uv pip install --python "{paths.CLONE_ENV}/bin/python" '
    "--extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match "
    '"coqui-tts[codec]" six torch torchaudio'
)


class CloneEngine:
    def __init__(self, license_accepted) -> None:
        self._license_accepted = license_accepted  # callable -> bool
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.ready = False

    @staticmethod
    def installed() -> bool:
        return (paths.CLONE_ENV / "bin" / "python").exists()

    def _start(self) -> None:
        if not self.installed():
            raise ClonePending("Il motore di clonazione non è installato (scheda «La mia voce»).")
        if not self._license_accepted():
            raise ClonePending("Accetta la licenza del modello XTTS nella scheda «La mia voce».")
        env = {**os.environ, "COQUI_TOS_AGREED": "1", "PYTHONNOUSERSITE": "1"}
        self._proc = subprocess.Popen(
            [str(paths.CLONE_ENV / "bin" / "python"), str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, start_new_session=True,
        )
        first = self._read()
        if not first.get("ready"):
            raise RuntimeError(first.get("error", "il motore di clonazione non parte"))
        self.ready = True

    def _read(self) -> dict:
        assert self._proc and self._proc.stdout
        line = self._proc.stdout.readline()
        if not line:
            self._proc = None
            self.ready = False
            raise RuntimeError("il motore di clonazione si è chiuso")
        return json.loads(line)

    def synthesize(self, sample: Path, text: str, out: Path, lang: str = "it", speed: float = 1.0) -> Path:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start()
            assert self._proc and self._proc.stdin
            req = {"text": text, "sample": str(sample), "out": str(out), "lang": lang, "speed": speed}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            resp = self._read()
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "sintesi non riuscita"))
        return out

    def close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
        self.ready = False


# --------------------------------------------------------------------------
# Sintesi
# --------------------------------------------------------------------------
def synthesize(voice_id: str, text: str, store: VoiceStore, clone: CloneEngine,
               rate: int = 0, lang: str = "it") -> Path:
    """Genera il file audio del testo con la voce indicata. `rate` va da -50 a +50 (%)."""
    paths.ensure_dirs()
    engine, _, name = voice_id.partition(":")
    out = paths.CACHE_DIR / uuid.uuid4().hex
    if engine == "espeak":
        wav = out.with_suffix(".wav")
        speed = max(80, int(175 * (1 + rate / 100)))
        subprocess.run(["espeak-ng", "-v", name, "-s", str(speed), "-w", str(wav), text], check=True)
        return wav
    if engine == "edge":
        import edge_tts

        mp3 = out.with_suffix(".mp3")
        asyncio.run(edge_tts.Communicate(text, name, rate=f"{rate:+d}%").save(str(mp3)))
        return mp3
    if engine == "clone":
        profile = next((p for p in store.profiles() if p.slug == name), None)
        if profile is None:
            raise RuntimeError("profilo voce non trovato")
        return clone.synthesize(profile.sample, text, out.with_suffix(".wav"), lang, 1 + rate / 100)
    raise ValueError(f"voce sconosciuta: {voice_id}")
