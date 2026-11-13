"""Processo di clonazione vocale (XTTS v2).

Gira con il Python dell'ambiente separato `clone-env` (non con quello dell'app) e
tiene il modello caricato tra una richiesta e l'altra. Parla con l'app a righe JSON:
stdin  <- {"text", "sample", "out", "lang", "speed"}
stdout -> {"ready": true} all'avvio, poi {"ok": true} oppure {"ok": false, "error": "..."}

Il campione della voce e l'audio generato restano su questo computer.
"""
import json
import os
import sys

# Il protocollo passa da un descrittore dedicato: le librerie possono stampare
# quello che vogliono su stdout/stderr senza rompere le risposte.
proto = os.fdopen(os.dup(1), "w")
sys.stdout = sys.stderr


def send(obj: dict) -> None:
    proto.write(json.dumps(obj) + "\n")
    proto.flush()


def main() -> None:
    try:
        import torch
        from TTS.api import TTS

        device = "cuda" if torch.cuda.is_available() else "cpu"
        tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    except Exception as exc:  # noqa: BLE001
        send({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return
    send({"ready": True, "device": device})

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            tts.tts_to_file(
                text=req["text"],
                speaker_wav=req["sample"],
                language=req.get("lang", "it"),
                file_path=req["out"],
                speed=float(req.get("speed", 1.0)),
            )
            send({"ok": True})
        except Exception as exc:  # noqa: BLE001
            send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
