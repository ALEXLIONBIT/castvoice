"""Processo di clonazione vocale (Qwen3-TTS 1.7B Base, licenza Apache-2.0).

Gira con il Python dell'ambiente separato `clone-env-qwen` e tiene il modello caricato
tra una richiesta e l'altra. Stesso protocollo del worker XTTS, a righe JSON:
stdin  <- {"text", "sample", "out", "lang", "speed", "ref_text"?}
stdout -> {"ready": true} all'avvio, poi {"ok": true} oppure {"ok": false, "error": "..."}

Se "ref_text" (la trascrizione del campione) manca si usa solo l'impronta del timbro,
con qualità un po' più bassa. Campione e audio generato restano su questo computer.
"""
import json
import os
import sys

proto = os.fdopen(os.dup(1), "w")
sys.stdout = sys.stderr

MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"
LANGS = {"it": "Italian", "en": "English", "fr": "French", "de": "German", "es": "Spanish",
         "pt": "Portuguese", "ru": "Russian", "ja": "Japanese", "ko": "Korean", "zh": "Chinese"}


def send(obj: dict) -> None:
    proto.write(json.dumps(obj) + "\n")
    proto.flush()


def main() -> None:
    try:
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel

        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        model = Qwen3TTSModel.from_pretrained(
            MODEL, device_map=device,
            dtype=torch.bfloat16 if device != "cpu" else torch.float32,
            attn_implementation="sdpa",
        )
    except Exception as exc:  # noqa: BLE001
        send({"ready": False, "error": f"{type(exc).__name__}: {exc}"})
        return
    send({"ready": True, "device": device})

    prompts: dict[tuple, object] = {}  # impronta della voce, riusata tra le richieste
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            ref_text = (req.get("ref_text") or "").strip()
            key = (req["sample"], ref_text)
            if key not in prompts:
                prompts[key] = model.create_voice_clone_prompt(
                    ref_audio=req["sample"], ref_text=ref_text or None,
                    x_vector_only_mode=not ref_text,
                )
            wavs, sr = model.generate_voice_clone(
                text=req["text"],
                language=LANGS.get(req.get("lang", "it"), "Auto"),
                voice_clone_prompt=prompts[key],
            )
            sf.write(req["out"], wavs[0], sr)
            send({"ok": True})
        except Exception as exc:  # noqa: BLE001
            send({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
