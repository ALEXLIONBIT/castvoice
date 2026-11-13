#!/bin/sh
# Avvia CastVoice usando l'ambiente virtuale del progetto.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
exec "$DIR/.venv/bin/python" -m castvoice "$@"
