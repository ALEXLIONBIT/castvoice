"""Punto d'ingresso dell'app impacchettata (PyInstaller): avvia CastVoice."""
import sys

from castvoice.app import run

if __name__ == "__main__":
    sys.exit(run())
