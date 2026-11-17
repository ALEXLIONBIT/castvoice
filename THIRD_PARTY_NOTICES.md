# Software di terze parti · Third-party software

CastVoice è sotto licenza MIT (vedi `LICENSE`). I pacchetti pronti (AppImage, zip per Windows e macOS) includono
anche software di altri, con le sue licenze. *CastVoice is MIT-licensed; the ready-made packages also bundle the
following third-party software under its own licences.*

| Software | Licenza | Dove | Note |
|---|---|---|---|
| **ffmpeg / ffprobe / ffplay** | GPL v3 (build statiche) | `bin/` dentro l'app | Build di [johnvansickle.com](https://johnvansickle.com/ffmpeg/) (Linux), [BtbN](https://github.com/BtbN/FFmpeg-Builds) (Windows), [evermeet.cx](https://evermeet.cx/ffmpeg/) (macOS). Codice sorgente: [ffmpeg.org](https://ffmpeg.org/download.html). È un programma separato, CastVoice lo avvia come processo. |
| **Qt 6 / PySide6** | LGPL v3 | cartella `PySide6/` dentro l'app | Le librerie sono collegate dinamicamente e si possono sostituire. Sorgenti: [qt.io](https://www.qt.io/download-open-source). |
| **PyInstaller** | GPL v2 con eccezione per i programmi impacchettati | avvio dell'app | [pyinstaller.org](https://pyinstaller.org/) |
| **pychromecast**, **zeroconf**, **edge-tts**, **aiohttp** | MIT / LGPL / Apache | Python | Ricerca e controllo dei Google Cast, voci online. |

**Non inclusi nel pacchetto, scaricati da te solo se li usi · Not bundled, downloaded on demand:**

- **Qwen3-TTS** (modello vocale per la clonazione) — Apache-2.0, da Hugging Face.
- **XTTS v2** (alternativa) — *Coqui Public Model License*, solo uso non commerciale.

La tua voce, i campioni e i profili restano sul tuo computer e non sono mai inclusi nei pacchetti né nel repository.
*Your voice, samples and profiles stay on your computer and are never included in packages or in the repository.*
