"""Percorsi dei dati dell'app.

Tutto ciò che è personale (registrazioni, campioni della voce, libreria audio)
vive in ~/.local/share/castvoice, FUORI dal repository, e non viene mai
pubblicato.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

APP_ID = "castvoice"

DATA_DIR = Path(os.environ.get("CASTVOICE_DATA", Path.home() / ".local/share" / APP_ID))
CLIPS_DIR = DATA_DIR / "clips"          # clip registrate dall'utente
VOICES_DIR = DATA_DIR / "voices"        # campioni per la clonazione (privati)
CACHE_DIR = DATA_DIR / "cache"          # audio generati (si può cancellare)
CLONE_ENV = DATA_DIR / "clone-env"      # ambiente Python separato per XTTS
LIBRARY_FILE = DATA_DIR / "library.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

PROJECT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_DIR / "assets"


def ensure_dirs() -> None:
    for d in (DATA_DIR, CLIPS_DIR, VOICES_DIR, CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)
        try:
            d.chmod(0o700)  # solo l'utente: ci sono dati personali
        except OSError:
            pass


def serve_dir() -> Path:
    """Cartella temporanea da cui il server HTTP locale serve l'audio ai Google."""
    d = Path(tempfile.gettempdir()) / f"{APP_ID}-serve-{os.getuid()}"
    d.mkdir(parents=True, exist_ok=True)
    return d
