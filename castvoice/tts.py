"""Sintesi vocale: voci locali, voci online e voce clonata.

Id delle voci:  "espeak:<voce>"  |  "edge:<ShortName>"  |  "clone:<slug profilo>"
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .library import VoiceStore

log = logging.getLogger("castvoice")

ESPEAK_VOICES = [  # (id espeak, nome, genere)
    ("it", "Robot classico", ""),
    ("it+m3", "Uomo", "uomo"),
    ("it+m7", "Uomo grave", "uomo"),
    ("it+f2", "Donna", "donna"),
    ("it+f4", "Donna acuta", "donna"),
    ("it+whisper", "Sussurro", ""),
    ("it+croak", "Rospo", ""),
]

LANG_NAMES = {"it": "Italiano", "en": "English", "es": "Español", "fr": "Français", "de": "Deutsch", "pt": "Português"}

# Frase dell'anteprima, nella lingua della voce.
SAMPLE_PHRASES = {
    "it": "Ciao, questa è la mia voce. Ti piace?",
    "en": "Hello, this is my voice. Do you like it?",
    "es": "Hola, esta es mi voz. ¿Te gusta?",
    "fr": "Bonjour, voici ma voix. Elle te plaît ?",
    "de": "Hallo, das ist meine Stimme. Gefällt sie dir?",
    "pt": "Olá, esta é a minha voz. Gostas?",
}

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
    name: str = ""
    lang: str = ""     # "it", "en"…; vuoto = qualsiasi lingua (voce clonata)
    gender: str = ""   # "donna" | "uomo" | ""
    locale: str = ""   # es. "it-IT"

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
    voices = [Voice(f"espeak:{v}", f"{name} · italiano · locale", "espeak", name, "it", gender, "it-IT")
              for v, name, gender in ESPEAK_VOICES] if shutil.which("espeak-ng") else []  # solo se installato
    for p in store.profiles():
        voices.insert(0, Voice(f"clone:{p.slug}", f"La mia voce – {p.name} · clonata", "clone", p.name))
    if online:
        for short, name, gender, locale in edge_voices():
            sesso = "donna" if gender == "Female" else "uomo"
            voices.append(Voice(f"edge:{short}", f"{name} ({sesso}, {locale}) · online", "edge",
                                name, locale.split("-")[0], sesso, locale))
    return voices


# --------------------------------------------------------------------------
# Clonazione (XTTS v2) in un processo separato, fuori dal progetto
# --------------------------------------------------------------------------
WORKER = Path(__file__).with_name("clone_worker.py")
QWEN_WORKER = Path(__file__).with_name("qwen_worker.py")
_UV_CUDA = (
    "--extra-index-url https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match "
)
# Modelli di clonazione disponibili: id -> (nome, ambiente Python, worker, comando di installazione)
CLONE_ENGINES = {
    "qwen": ("Qwen3-TTS 1.7B", paths.CLONE_ENV_QWEN, QWEN_WORKER,
             f'uv venv --python 3.12 "{paths.CLONE_ENV_QWEN}" && '
             f'uv pip install --python "{paths.env_python(paths.CLONE_ENV_QWEN)}" {_UV_CUDA}'
             "qwen-tts torch torchaudio soundfile huggingface_hub"),
    "xtts": ("XTTS v2", paths.CLONE_ENV, WORKER,
             f'uv venv --python 3.12 "{paths.CLONE_ENV}" && '
             f'uv pip install --python "{paths.env_python(paths.CLONE_ENV)}" {_UV_CUDA}'
             '"coqui-tts[codec]" six torch torchaudio'),
}
DEFAULT_CLONE_ENGINE = "qwen"
CLONE_INSTALL_CMD = CLONE_ENGINES["xtts"][3]  # compatibilità con il vecchio nome


class CloneEngine:
    def __init__(self, license_accepted, engine=lambda: DEFAULT_CLONE_ENGINE) -> None:
        self._license_accepted = license_accepted  # callable -> bool (licenza XTTS)
        self._engine = engine                      # callable -> "qwen" | "xtts" (scelta dell'utente)
        self._running: str | None = None           # modello attualmente caricato nel processo
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self.ready = False
        self.on_stage = None   # callable(str) chiamata (da un thread di lavoro) quando cambia fase: "load" | "synth"
        self.timing: dict[str, float] = {}  # durate misurate dell'ultima richiesta: "load", "synth" (secondi)

    def _notify(self, stage: str) -> None:
        cb = self.on_stage
        if cb:
            try:
                cb(stage)
            except Exception:  # noqa: BLE001 - un problema dell'interfaccia non deve fermare la sintesi
                log.exception("on_stage fallita")

    def needs_load(self) -> bool:
        """True se la prossima sintesi deve prima caricare il modello (lento)."""
        return self._proc is None or self._proc.poll() is not None or self._running != self._wanted()

    @staticmethod
    def engine_installed(engine: str) -> bool:
        return paths.env_python(CLONE_ENGINES[engine][1]).exists()

    @staticmethod
    def qwen_installed() -> bool:
        return CloneEngine.engine_installed("qwen")

    @staticmethod
    def installed() -> bool:
        return any(CloneEngine.engine_installed(e) for e in CLONE_ENGINES)

    def _wanted(self) -> str:
        e = self._engine()
        return e if e in CLONE_ENGINES else DEFAULT_CLONE_ENGINE

    def _start(self, engine: str) -> None:
        name, env_dir, worker, _ = CLONE_ENGINES[engine]
        if not self.engine_installed(engine):
            raise ClonePending(f"Il modello {name} non è installato (scheda «La mia voce»).")
        if engine == "xtts" and not self._license_accepted():
            raise ClonePending("Accetta la licenza del modello XTTS nella scheda «La mia voce».")
        env = {**os.environ, "COQUI_TOS_AGREED": "1", "PYTHONNOUSERSITE": "1"}
        self._notify("load")
        t0 = time.monotonic()
        self._proc = subprocess.Popen(
            [str(paths.env_python(env_dir)), str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, start_new_session=True,
        )
        first = self._read()
        if not first.get("ready"):
            raise RuntimeError(first.get("error", "il motore di clonazione non parte"))
        self.timing["load"] = time.monotonic() - t0
        self._running = engine
        self.ready = True

    def _read(self) -> dict:
        assert self._proc and self._proc.stdout
        line = self._proc.stdout.readline()
        if not line:
            self._proc = None
            self.ready = False
            raise RuntimeError("il motore di clonazione si è chiuso")
        return json.loads(line)

    def synthesize(self, sample: Path, text: str, out: Path, lang: str = "it", speed: float = 1.0,
                   ref_text: str = "") -> Path:
        with self._lock:
            wanted = self._wanted()
            if self._proc is not None and self._running != wanted:
                self._stop()  # l'utente ha cambiato modello: scarica quello vecchio dalla GPU
            if self._proc is None or self._proc.poll() is not None:
                self._start(wanted)
            self._notify("synth")
            t0 = time.monotonic()
            assert self._proc and self._proc.stdin
            req = {"text": text, "sample": str(sample), "out": str(out), "lang": lang, "speed": speed,
                   "ref_text": ref_text}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            resp = self._read()
            self.timing["synth"] = time.monotonic() - t0
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "sintesi non riuscita"))
        return out

    def _stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
        self._running = None
        self.ready = False

    def release(self) -> None:
        """Libera la GPU dopo un cambio di modello, senza bloccare l'interfaccia se è in corso una sintesi."""
        if self._lock.acquire(blocking=False):
            try:
                self._stop()
            finally:
                self._lock.release()

    def close(self) -> None:
        self._stop()


def join_audio(parts: list[Path], gap: float = 0.18) -> Path:
    """Unisce più audio (anche di formati diversi) in uno solo, con una piccola pausa tra uno e l'altro."""
    if len(parts) == 1:
        return parts[0]
    paths.ensure_dirs()
    out = paths.CACHE_DIR / f"{uuid.uuid4().hex}.wav"
    cmd = [paths.tool("ffmpeg"), "-y", "-loglevel", "error"]
    for p in parts:
        cmd += ["-i", str(p)]
    chains = [
        f"[{i}:a]aresample=24000,aformat=sample_fmts=s16:channel_layouts=mono"
        + (f",apad=pad_dur={gap}" if i < len(parts) - 1 else "") + f"[a{i}]"
        for i in range(len(parts))
    ]
    graph = ";".join(chains) + ";" + "".join(f"[a{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=0:a=1[o]"
    subprocess.run(cmd + ["-filter_complex", graph, "-map", "[o]", str(out)], check=True)
    return out


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
        try:
            asyncio.run(edge_tts.Communicate(text, name, rate=f"{rate:+d}%").save(str(mp3)))
        except Exception as exc:  # noqa: BLE001 - rete assente, servizio cambiato, ecc.
            raise RuntimeError(
                "La voce online non risponde (serve internet; se persiste aggiorna edge-tts). "
                f"Prova una voce locale 💻. Dettaglio: {type(exc).__name__}"
            ) from exc
        return mp3
    if engine == "clone":
        profile = next((p for p in store.profiles() if p.slug == name), None)
        if profile is None:
            raise RuntimeError("profilo voce non trovato")
        return clone.synthesize(profile.sample, text, out.with_suffix(".wav"), lang, 1 + rate / 100,
                                profile.ref_text)
    raise ValueError(f"voce sconosciuta: {voice_id}")
