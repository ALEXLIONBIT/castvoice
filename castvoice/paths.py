"""Percorsi dei dati dell'app.

Tutto ciò che è personale (registrazioni, campioni della voce, libreria audio)
vive nella cartella dei dati dell'utente, FUORI dal repository, e non viene mai
pubblicato. Linux: ~/.local/share/castvoice · Windows: %APPDATA%\\castvoice ·
macOS: ~/Library/Application Support/castvoice.
"""
from __future__ import annotations

import getpass
import os
import shutil
import sys
import tempfile
from pathlib import Path

APP_ID = "castvoice"


def _default_data_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_ID
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


DATA_DIR = Path(os.environ.get("CASTVOICE_DATA", _default_data_dir()))
CLIPS_DIR = DATA_DIR / "clips"          # clip registrate dall'utente
VOICES_DIR = DATA_DIR / "voices"        # campioni per la clonazione (privati)
CACHE_DIR = DATA_DIR / "cache"          # audio generati (si può cancellare)
CLONE_ENV = DATA_DIR / "clone-env"      # ambiente Python separato per XTTS
CLONE_ENV_QWEN = DATA_DIR / "clone-env-qwen"  # ambiente separato per Qwen3-TTS (preferito se presente)
LIBRARY_FILE = DATA_DIR / "library.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "assets"


def bundle_dir() -> Path | None:
    """Cartella dell'app impacchettata (PyInstaller), o None se gira dal codice sorgente."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else None


def tool(name: str) -> str:
    """Percorso di ffmpeg / ffprobe / ffplay: quello incluso nell'app se c'è, altrimenti quello del sistema."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    bundle = bundle_dir()
    if bundle and (bundle / "bin" / exe).exists():
        return str(bundle / "bin" / exe)
    return shutil.which(name) or name


def env_python(env_dir: Path) -> Path:
    """L'eseguibile Python dentro un ambiente virtuale creato con uv/venv."""
    return env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def ensure_dirs() -> None:
    for d in (DATA_DIR, CLIPS_DIR, VOICES_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)  # solo l'utente: ci sono dati personali
        except OSError:
            pass


def serve_dir() -> Path:
    """Cartella temporanea da cui il server HTTP locale serve l'audio ai Google."""
    uid = os.getuid() if hasattr(os, "getuid") else getpass.getuser()  # Windows non ha getuid
    d = Path(tempfile.gettempdir()) / f"{APP_ID}-serve-{uid}"
    d.mkdir(parents=True, exist_ok=True)
    return d
