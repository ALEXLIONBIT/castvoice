# CastVoice

**Desktop app to make your Google Nest / Google Cast speakers talk — type a message, pick a voice (even your own), or play any audio file on one or many speakers at once.**

*App desktop per far parlare i tuoi Google Nest / Google Cast: scrivi un messaggio, scegli una voce (anche la tua) oppure riproduci qualsiasi file audio su uno o più altoparlanti insieme.*

![CastVoice icon](assets/castvoice.svg)

## Scarica · Download

Vai alla pagina **[Releases](https://github.com/ALEXLIONBIT/castvoice/releases/latest)** e prendi il file per il tuo sistema, senza installare altro (ffmpeg è incluso):

| Sistema | File |
|---|---|
| 🐧 **Linux** (x86_64) | `.AppImage`: rendilo eseguibile e doppio clic |
| 🪟 **Windows** (64 bit) | `.zip`: estrai e apri `CastVoice.exe` |
| 🍎 **macOS** (Apple Silicon) | `.zip`: estrai e apri `CastVoice.app` (la prima volta: tasto destro → Apri) |
| 🤖 **Android** | `.apk`: copialo sul telefono e consenti «installa app sconosciute» |

Windows e macOS non sono firmati, quindi il sistema può mostrare un avviso. Le build per Windows e macOS sono prodotte automaticamente da GitHub Actions. *Pick the file for your system from the latest release; no extra install needed.* Licenze dei componenti inclusi: [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

## Funzioni · Features

- 🗣 **Più voci nello stesso messaggio**: scegli una voce, scrivi, cambia voce e continua; **Alt+1…Alt+9** (o i pulsanti colorati) cambiano voce al volo · several voices in one message, switch with Alt+1…9

- 🔎 **Trova da sola** i Google Nest, Chromecast e gruppi sulla rete locale · finds speakers on your LAN
- 🔈 **Volume** per ogni dispositivo, selezione multipla · per-device volume, multi-select
- 💬 **Parla**: scrivi un testo e scegli la voce · type text, pick a voice
  - voci locali (`espeak-ng`), senza internet · offline voices
  - voci neurali online (Microsoft Edge TTS: italiano, inglese, spagnolo, francese, tedesco, portoghese) · online neural voices
  - **la tua voce clonata** (Qwen3-TTS, licenza Apache-2.0, oppure XTTS v2; sulla tua GPU, scelta dalla scheda «La mia voce») · **your own cloned voice**
  - velocità regolabile e **frasi rapide** salvate · speed control and saved quick phrases
  - **scelta voce organizzata** (oltre 130 voci): filtri per tipo e lingua, ricerca per nome/uomo/donna, ★ preferite e **🔊 anteprima sul PC** (doppio clic) prima di mandarla ai Google · organised voice picker with filters, search, favourites and local preview
- 🎵 **Audio**: trascina i tuoi file (mp3, wav, ogg, flac, m4a…) e riproducili sui Google · drag & drop your files
- 🎬 **Video → solo audio**: aggiungi un video (mp4, mkv, webm, mov, avi…) e sui Google si sente solo la sua traccia audio · add a video and only its soundtrack is played
- 🎙 **Registra clip** con il microfono e riproducile quando vuoi · record voice clips and play them anytime

## App Android · Android app

📱 Nella cartella [`android/`](android/) c'è la versione **mobile** (APK): Kotlin + Jetpack Compose + Google Cast SDK. *The `android/` folder holds the mobile version (APK).*

- 🔎 trova i Google Nest / Chromecast sulla rete, volume e stop · finds speakers, volume, stop
- 💬 **Parla** con le voci del telefono («Sintesi vocale Google»: offline e online di alta qualità), con la stessa **scelta voce organizzata** (filtri tipo/lingua, ricerca, ★ preferite, 🔊 anteprima) e frasi rapide · speak with the phone's TTS voices
- 🎵 **Audio** e 🎬 **video → solo audio** dai file del telefono · audio files and video soundtracks
- 🎙 **Registra clip** con il microfono e riproducili sui Google · record clips and play them on the speakers
- Il telefono serve i file ai Google con un mini server HTTP locale (solo finché l'app è aperta) · local HTTP server, only while the app is open

Limiti rispetto al desktop: un dispositivo alla volta (Cast SDK), niente voci Microsoft Edge e niente clonazione della voce (restano sul PC). *Limits: one speaker at a time; no Edge voices and no voice cloning (desktop only).*

**Installare l'APK**: copialo sul telefono, apri il file e consenti «installa app sconosciute» per quell'app. Telefono e Google devono stare sullo stesso Wi-Fi.

**Compilarlo** (JDK 17/21 + Android SDK, `platforms;android-35`, `build-tools;35.0.0`):

```bash
cd android
./gradlew assembleRelease     # → app/build/outputs/apk/release/app-release.apk
```

La firma usa una chiave **tua**, fuori dal repository: crea `~/.android/castvoice-signing.properties` (`storeFile`, `storePassword`, `keyAlias`, `keyPassword`) con `keytool -genkeypair …`. Le registrazioni restano nella memoria privata dell'app e il backup cloud è disattivato.

## Privacy

La tua voce **non lascia mai il tuo computer**: registrazioni, campioni e profili vivono in
`~/.local/share/castvoice/`, fuori dal progetto, e `.gitignore` esclude ogni file audio.
La clonazione vocale gira in locale. Solo le voci «online» mandano il *testo* (mai la tua voce) al servizio di sintesi Microsoft Edge.

Your voice never leaves your computer: recordings and samples live in `~/.local/share/castvoice/`
(outside the repo). Only the *text* is sent to Microsoft when you choose an "online" voice.

## Installazione · Install

Requisiti · Requirements: Linux, Python ≥ 3.10, `ffmpeg`, `espeak-ng`, PipeWire (`pw-record`) per registrare · to record. Per clonare la voce: [`uv`](https://docs.astral.sh/uv/) e una GPU NVIDIA consigliata · for voice cloning.

```bash
git clone https://github.com/ALEXLIONBIT/castvoice.git
cd castvoice
./install.sh              # ambiente + icona + voce nel menu
./install.sh --pin-kde    # in più: icona fissata sulla barra di KDE Plasma
```

Poi apri **CastVoice** dal menu delle applicazioni, oppure `./run.sh`.

## Come si usa · Usage

1. Premi **⟳ Cerca** e spunta i dispositivi a sinistra · tick the speakers.
2. Scheda **Parla**: scrivi, scegli la voce, **▶ Parla** (`Ctrl+Invio`).
3. Scheda **Audio**: trascina i file, doppio clic per riprodurre.
4. Scheda **La mia voce**: installa il motore di clonazione una volta, accetta la licenza del modello, registra ~20 secondi di voce e scegli «La mia voce» tra le voci.

## Come funziona · How it works

I Google Cast non ricevono l'audio direttamente: gli si dà un URL e lo scaricano loro. CastVoice avvia un mini server HTTP locale (legato alla sola interfaccia di rete che raggiunge il dispositivo, nomi file casuali) e dice al dispositivo di riprodurre quell'URL tramite il protocollo Cast ([`pychromecast`](https://github.com/home-assistant-libs/pychromecast)). Il PC e i Google devono stare sulla stessa rete.

## Licenze · Licenses

Il codice è MIT. Il modello **XTTS v2** (clonazione) usa la *Coqui Public Model License*, **solo uso non commerciale**: viene scaricato solo dopo che accetti la licenza nell'app. Le voci Edge TTS sono un servizio Microsoft soggetto ai loro termini.

Code is MIT. The XTTS v2 model is non-commercial (CPML) and is only downloaded after you accept its licence in the app.
