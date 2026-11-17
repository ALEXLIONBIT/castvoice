"""Libreria audio, impostazioni, profili voce e registratore.

Tutti i dati personali stanno in ~/.local/share/castvoice (vedi paths.py),
mai dentro il repository.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import paths


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, data) -> None:
    paths.ensure_dirs()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Impostazioni
# --------------------------------------------------------------------------
class Settings:
    DEFAULTS = {
        "devices": [],                              # uuid dei dispositivi spuntati
        "voice": "edge:it-IT-IsabellaNeural",       # id voce scelta
        "rate": 0,                                  # velocità: -50..+50 (%)
        "phrases": [],                              # frasi rapide [{text, voice}]
        "favorite_voices": [],                      # id delle voci preferite
        "xtts_license_accepted": False,             # licenza CPML del modello XTTS
        "clone_engine": "qwen",                     # modello di clonazione: "qwen" | "xtts"
        "timing": {},                               # tempi misurati (s) per stimare la barra: load, synth_per_char
        "voice_slots": None,                        # id delle voci su Alt+1…Alt+9 (None = automatico)
        "mic": "",                                  # sorgente audio per registrare
    }

    def __init__(self) -> None:
        self._data = {**self.DEFAULTS, **_read_json(paths.SETTINGS_FILE, {})}

    def get(self, key: str):
        return self._data.get(key, self.DEFAULTS.get(key))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        _write_json(paths.SETTINGS_FILE, self._data)


# --------------------------------------------------------------------------
# Libreria audio (file importati + clip registrate)
# --------------------------------------------------------------------------
@dataclass
class Item:
    id: str
    name: str
    path: str
    kind: str   # "file" (percorso esterno) oppure "clip" (registrata, privata)
    added: str

    @property
    def exists(self) -> bool:
        return Path(self.path).exists()


class Library:
    def __init__(self) -> None:
        raw = _read_json(paths.LIBRARY_FILE, {"items": []})
        self._items = [Item(**i) for i in raw.get("items", [])]

    def _save(self) -> None:
        _write_json(paths.LIBRARY_FILE, {"items": [asdict(i) for i in self._items]})

    def items(self) -> list[Item]:
        return list(self._items)

    def get(self, item_id: str) -> Item | None:
        return next((i for i in self._items if i.id == item_id), None)

    def add_file(self, path: Path) -> Item:
        item = Item(uuid.uuid4().hex[:8], path.stem, str(path.resolve()), "file", _now())
        self._items.append(item)
        self._save()
        return item

    def add_clip(self, name: str, wav: Path) -> Item:
        paths.ensure_dirs()
        item_id = uuid.uuid4().hex[:8]
        dest = paths.CLIPS_DIR / f"{item_id}{wav.suffix}"
        shutil.move(str(wav), dest)
        item = Item(item_id, name, str(dest), "clip", _now())
        self._items.append(item)
        self._save()
        return item

    def remove(self, item_id: str) -> None:
        item = self.get(item_id)
        if item is None:
            return
        if item.kind == "clip":  # le clip sono nostre: si cancella anche il file
            Path(item.path).unlink(missing_ok=True)
        self._items = [i for i in self._items if i.id != item_id]
        self._save()


# --------------------------------------------------------------------------
# Profili voce per la clonazione (privati)
# --------------------------------------------------------------------------
@dataclass
class Profile:
    slug: str
    name: str
    created: str

    @property
    def sample(self) -> Path:
        return paths.VOICES_DIR / self.slug / "sample.wav"

    @property
    def ref_text(self) -> str:
        """Trascrizione del campione (opzionale, migliora la clonazione con Qwen3-TTS)."""
        meta = _read_json(paths.VOICES_DIR / self.slug / "meta.json", {})
        return meta.get("ref_text", "") if isinstance(meta, dict) else ""


class VoiceStore:
    def profiles(self) -> list[Profile]:
        out = []
        if not paths.VOICES_DIR.exists():
            return out
        for d in sorted(paths.VOICES_DIR.iterdir()):
            meta = _read_json(d / "meta.json", None)
            if meta and (d / "sample.wav").exists():
                out.append(Profile(d.name, meta.get("name", d.name), meta.get("created", "")))
        return out

    def add(self, name: str, sample_src: Path) -> Profile:
        """Salva il campione come WAV mono 24 kHz (adatto a XTTS)."""
        paths.ensure_dirs()
        slug = f"{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'voce'}-{uuid.uuid4().hex[:6]}"
        folder = paths.VOICES_DIR / slug
        folder.mkdir(parents=True, exist_ok=True)
        folder.chmod(0o700)
        subprocess.run(
            [paths.tool("ffmpeg"), "-y", "-loglevel", "error", "-i", str(sample_src), "-ac", "1", "-ar", "24000", str(folder / "sample.wav")],
            check=True,
        )
        profile = Profile(slug, name, _now())
        _write_json(folder / "meta.json", {"name": profile.name, "created": profile.created})
        return profile

    def remove(self, slug: str) -> None:
        shutil.rmtree(paths.VOICES_DIR / slug, ignore_errors=True)


# --------------------------------------------------------------------------
# Registratore: PipeWire su Linux, ffmpeg (DirectShow / AVFoundation) su Windows e macOS
# --------------------------------------------------------------------------
def _ffmpeg_audio_sources() -> list[tuple[str, str]]:
    """Microfoni su Windows (DirectShow) e macOS (AVFoundation), come li elenca ffmpeg."""
    if sys.platform == "win32":
        cmd = [paths.tool("ffmpeg"), "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    else:
        cmd = [paths.tool("ffmpeg"), "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
    try:
        text = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    except OSError:
        return []
    out: list[tuple[str, str]] = []
    if sys.platform == "win32":
        for m in re.finditer(r'"([^"]+)"\s+\(audio\)', text):
            out.append((m.group(1), m.group(1)))
    else:
        in_audio = False
        for line in text.splitlines():
            if "AVFoundation audio devices" in line:
                in_audio = True
            elif in_audio and (m := re.search(r"\[(\d+)\]\s+(.+)$", line)):
                out.append((m.group(1), m.group(2).strip()))
    return out


def audio_sources() -> list[tuple[str, str]]:
    """Microfoni disponibili: [(nome_tecnico, descrizione)]."""
    if not sys.platform.startswith("linux"):
        return _ffmpeg_audio_sources()
    try:
        text = subprocess.run(["pactl", "list", "sources"], capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for block in text.split("Source #")[1:]:
        name = re.search(r"^\s*Name: (.+)$", block, re.M)
        desc = re.search(r"^\s*Description: (.+)$", block, re.M)
        if name and not name.group(1).endswith(".monitor"):
            out.append((name.group(1), desc.group(1) if desc else name.group(1)))
    return out


class Recorder:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._out: Path | None = None
        self._t0 = 0.0

    @property
    def recording(self) -> bool:
        return self._proc is not None

    @property
    def elapsed(self) -> float:
        return time.time() - self._t0 if self._proc else 0.0

    def start(self, out: Path, source: str = "") -> None:
        stdin = None
        if sys.platform.startswith("linux"):
            cmd = ["pw-record", "--rate", "24000", "--channels", "1", "--format", "s16"]
            if source:
                cmd += ["--target", source]
            cmd.append(str(out))
        else:  # ffmpeg: si ferma scrivendo «q» sul suo input
            if not source:
                found = _ffmpeg_audio_sources()
                if not found:
                    raise RuntimeError("Non trovo nessun microfono.")
                source = found[0][0]
            fmt = ["-f", "dshow", "-i", f"audio={source}"] if sys.platform == "win32" else ["-f", "avfoundation", "-i", f":{source}"]
            cmd = [paths.tool("ffmpeg"), "-y", "-loglevel", "error", *fmt, "-ar", "24000", "-ac", "1", str(out)]
            stdin = subprocess.PIPE
        self._out = out
        self._t0 = time.time()
        self._proc = subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=sys.platform != "win32")

    def stop(self) -> Path | None:
        """Ferma la registrazione; ritorna il file se contiene audio."""
        proc, out = self._proc, self._out
        self._proc = None
        if proc is None or out is None:
            return None
        if proc.stdin is not None:  # ffmpeg chiude bene il file se riceve «q»
            try:
                proc.stdin.write(b"q")
                proc.stdin.flush()
            except OSError:
                pass
        else:
            proc.send_signal(signal.SIGINT)  # pw-record chiude bene il WAV con Ctrl-C
        try:
            proc.wait(timeout=6)
        except subprocess.TimeoutExpired:
            proc.kill()
        return out if out.exists() and out.stat().st_size > 4000 else None
