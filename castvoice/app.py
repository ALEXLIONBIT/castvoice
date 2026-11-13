"""Interfaccia grafica di CastVoice (PySide6)."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSlider, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from . import paths, tts
from .engine import Device, Player, discover, prepare_audio
from .library import Library, Recorder, Settings, VoiceStore, audio_sources

log = logging.getLogger("castvoice")

AUDIO_EXT = {".mp3", ".wav", ".ogg", ".opus", ".flac", ".m4a", ".aac", ".wma", ".webm", ".mp4"}
SAMPLE_TEXT = (
    "Ciao, sono io. Oggi è una bella giornata e sto registrando un campione della mia voce, "
    "parlando con calma e in modo naturale. Più il campione è pulito, senza rumori di fondo, "
    "meglio verrà la mia voce clonata."
)

STYLE = """
* { font-size: 14px; }
QWidget { background: #0f1420; color: #e8ecf4; }
QMainWindow, QDialog { background: #0f1420; }
QFrame#panel { background: #161c2b; border: 1px solid #232b3f; border-radius: 12px; }
QLabel#title { font-size: 17px; font-weight: 700; }
QLabel#muted { color: #8a94a8; }
QLabel#note { color: #9fb3c8; background: #131a2a; border: 1px solid #232b3f; border-radius: 8px; padding: 8px; }
QTabWidget::pane { border: 1px solid #232b3f; border-radius: 12px; top: -1px; background: #161c2b; }
QTabBar::tab { background: #0f1420; color: #8a94a8; padding: 9px 18px; margin-right: 4px;
               border-top-left-radius: 8px; border-top-right-radius: 8px; }
QTabBar::tab:selected { background: #161c2b; color: #22d3ee; font-weight: 700; }
QPushButton { background: #232b3f; border: none; border-radius: 8px; padding: 8px 14px; }
QPushButton:hover { background: #2d3752; }
QPushButton:disabled { color: #566078; background: #1a2133; }
QPushButton#accent { background: #22d3ee; color: #06222a; font-weight: 700; }
QPushButton#accent:hover { background: #5be3f5; }
QPushButton#accent:disabled { background: #1b4a55; color: #6b8b93; }
QPushButton#danger { background: #3a1f2b; color: #ff8aa1; }
QPlainTextEdit, QLineEdit, QComboBox, QListWidget { background: #0f1420; border: 1px solid #2a3350;
               border-radius: 8px; padding: 6px; selection-background-color: #22d3ee; selection-color: #06222a; }
QComboBox QAbstractItemView { background: #161c2b; selection-background-color: #22d3ee; selection-color: #06222a; }
QListWidget::item { padding: 6px; border-radius: 6px; }
QListWidget::item:selected { background: #1f3b4a; color: #e8ecf4; }
QSlider::groove:horizontal { height: 6px; background: #2a3350; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #22d3ee; border-radius: 3px; }
QSlider::handle:horizontal { background: #a78bfa; width: 16px; margin: -6px 0; border-radius: 8px; }
QCheckBox::indicator { width: 18px; height: 18px; border-radius: 5px; border: 1px solid #2a3350; background: #0f1420; }
QCheckBox::indicator:checked { background: #22d3ee; border-color: #22d3ee; }
QScrollArea { border: none; }
QProgressBar { background: #0f1420; border: 1px solid #2a3350; border-radius: 6px; height: 8px; text-align: center; }
QProgressBar::chunk { background: #a78bfa; border-radius: 5px; }
QStatusBar { background: #0b0f19; color: #8a94a8; }
"""


# --------------------------------------------------------------------------
# Lavori in background
# --------------------------------------------------------------------------
class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class Task(QRunnable):
    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001 - l'errore va mostrato all'utente
            log.exception("task fallito")
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.done.emit(result)


# --------------------------------------------------------------------------
# Pannello dispositivi
# --------------------------------------------------------------------------
class DeviceRow(QFrame):
    volume_changed = Signal(object, int)
    toggled = Signal()

    def __init__(self, device: Device, checked: bool) -> None:
        super().__init__()
        self.device = device
        self.setObjectName("panel")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)
        top = QHBoxLayout()
        self.check = QCheckBox(device.name)
        self.check.setChecked(checked)
        self.check.setStyleSheet("font-weight: 700;")
        self.check.toggled.connect(lambda _=None: self.toggled.emit())
        kind = "Gruppo" if device.is_group else device.model.replace("Google ", "")
        sub = QLabel(kind)
        sub.setObjectName("muted")
        top.addWidget(self.check, 1)
        top.addWidget(sub)
        lay.addLayout(top)
        vol = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setEnabled(False)
        self.label = QLabel("–")
        self.label.setObjectName("muted")
        self.label.setFixedWidth(40)
        self.label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        vol.addWidget(QLabel("🔈"))
        vol.addWidget(self.slider, 1)
        vol.addWidget(self.label)
        lay.addLayout(vol)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(350)
        self._debounce.timeout.connect(lambda: self.volume_changed.emit(self.device, self.slider.value()))
        self.slider.valueChanged.connect(self._on_slide)

    def _on_slide(self, value: int) -> None:
        self.label.setText(f"{value}%")
        self._debounce.start()

    def set_volume(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.slider.setEnabled(True)
        self.label.setText(f"{value}%")


class DevicePanel(QWidget):
    refresh_requested = Signal()
    selection_changed = Signal()
    volume_changed = Signal(object, int)

    def __init__(self) -> None:
        super().__init__()
        self.rows: dict[str, DeviceRow] = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("Dispositivi")
        title.setObjectName("title")
        self.refresh_btn = QPushButton("⟳ Cerca")
        self.refresh_btn.clicked.connect(self.refresh_requested)
        head.addWidget(title, 1)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        lay.addWidget(self.info)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.vbox = QVBoxLayout(self.inner)
        self.vbox.setContentsMargins(0, 0, 4, 0)
        self.vbox.setSpacing(8)
        self.vbox.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        btns = QHBoxLayout()
        all_btn, none_btn = QPushButton("Tutti"), QPushButton("Nessuno")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn.clicked.connect(lambda: self._set_all(False))
        btns.addWidget(all_btn)
        btns.addWidget(none_btn)
        lay.addLayout(btns)

    def set_devices(self, devices: list[Device], checked_uuids: list[str]) -> None:
        for row in self.rows.values():
            row.setParent(None)
            row.deleteLater()
        self.rows.clear()
        for d in devices:
            row = DeviceRow(d, d.uuid in checked_uuids)
            row.toggled.connect(self.selection_changed)
            row.volume_changed.connect(self.volume_changed)
            self.vbox.insertWidget(self.vbox.count() - 1, row)
            self.rows[d.uuid] = row
        self.info.setText(f"{len(devices)} trovati" if devices else "Nessun dispositivo trovato")

    def checked(self) -> list[Device]:
        return [r.device for r in self.rows.values() if r.check.isChecked()]

    def _set_all(self, value: bool) -> None:
        for r in self.rows.values():
            r.check.blockSignals(True)
            r.check.setChecked(value)
            r.check.blockSignals(False)
        self.selection_changed.emit()


# --------------------------------------------------------------------------
# Registrazione
# --------------------------------------------------------------------------
class RecordDialog(QDialog):
    """Registra dal microfono. Dopo «Salva» ha `.name` e `.wav`."""

    def __init__(self, parent, title: str, hint: str, default_name: str, settings: Settings) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(460)
        self.settings = settings
        self.recorder = Recorder()
        self.name = ""
        self.wav: Path | None = None
        paths.ensure_dirs()
        self._tmp = paths.CACHE_DIR / f"rec-{int(time.time())}.wav"

        lay = QVBoxLayout(self)
        if hint:
            note = QLabel(hint)
            note.setObjectName("note")
            note.setWordWrap(True)
            lay.addWidget(note)
        self.name_edit = QLineEdit(default_name)
        lay.addWidget(QLabel("Nome"))
        lay.addWidget(self.name_edit)
        lay.addWidget(QLabel("Microfono"))
        self.mic = QComboBox()
        self.mic.addItem("Predefinito del sistema", "")
        for name, desc in audio_sources():
            self.mic.addItem(desc, name)
        idx = self.mic.findData(settings.get("mic"))
        self.mic.setCurrentIndex(max(0, idx))
        lay.addWidget(self.mic)
        self.timer_label = QLabel("00:00")
        self.timer_label.setAlignment(Qt.AlignCenter)
        self.timer_label.setStyleSheet("font-size: 30px; font-weight: 700;")
        lay.addWidget(self.timer_label)
        row = QHBoxLayout()
        self.rec_btn = QPushButton("● Registra")
        self.rec_btn.setObjectName("accent")
        self.rec_btn.clicked.connect(self._toggle)
        self.save_btn = QPushButton("Salva")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self._save)
        cancel = QPushButton("Annulla")
        cancel.clicked.connect(self.reject)
        row.addWidget(self.rec_btn, 1)
        row.addWidget(self.save_btn)
        row.addWidget(cancel)
        lay.addLayout(row)
        self._tick = QTimer(self)
        self._tick.setInterval(200)
        self._tick.timeout.connect(self._update_timer)

    def _update_timer(self) -> None:
        s = int(self.recorder.elapsed)
        self.timer_label.setText(f"{s // 60:02d}:{s % 60:02d}")

    def _toggle(self) -> None:
        if not self.recorder.recording:
            self.settings.set("mic", self.mic.currentData())
            self.recorder.start(self._tmp, self.mic.currentData())
            self._tick.start()
            self.rec_btn.setText("■ Stop")
            self.save_btn.setEnabled(False)
            self.mic.setEnabled(False)
        else:
            self._tick.stop()
            self.wav = self.recorder.stop()
            self.rec_btn.setText("● Registra di nuovo")
            self.mic.setEnabled(True)
            self.save_btn.setEnabled(self.wav is not None)
            if self.wav is None:
                QMessageBox.warning(self, "Registrazione vuota", "Non ho sentito niente: controlla il microfono.")

    def _save(self) -> None:
        self.name = self.name_edit.text().strip() or "Senza nome"
        self.accept()

    def reject(self) -> None:
        if self.recorder.recording:
            self.recorder.stop()
        self._tmp.unlink(missing_ok=True)
        super().reject()


class DropList(QListWidget):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)

    def dragEnterEvent(self, e) -> None:
        e.acceptProposedAction() if e.mimeData().hasUrls() else e.ignore()

    def dragMoveEvent(self, e) -> None:
        e.acceptProposedAction() if e.mimeData().hasUrls() else e.ignore()

    def dropEvent(self, e) -> None:
        files = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        self.files_dropped.emit([f for f in files if f.suffix.lower() in AUDIO_EXT])


# --------------------------------------------------------------------------
# Finestra principale
# --------------------------------------------------------------------------
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        paths.ensure_dirs()
        self.setWindowTitle("CastVoice")
        self.resize(1040, 680)
        self.settings = Settings()
        self.library = Library()
        self.store = VoiceStore()
        self.player = Player()
        self.clone = tts.CloneEngine(lambda: bool(self.settings.get("xtts_license_accepted")))
        self.pool = QThreadPool.globalInstance()
        self._tasks: set[Task] = set()
        self.voices: list[tts.Voice] = []
        self.install_proc: QProcess | None = None
        self._busy = 0

        self.panel = DevicePanel()
        self.panel.refresh_requested.connect(self.refresh_devices)
        self.panel.selection_changed.connect(self._save_selection)
        self.panel.volume_changed.connect(self._set_volume)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_speak_tab(), "💬  Parla")
        self.tabs.addTab(self._build_audio_tab(), "🎵  Audio")
        self.tabs.addTab(self._build_voice_tab(), "🎙  La mia voce")

        left = QFrame()
        left.setObjectName("panel")
        left.setMinimumWidth(310)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.addWidget(self.panel)
        split = QSplitter()
        split.addWidget(left)
        split.addWidget(self.tabs)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([320, 720])
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(12, 12, 12, 8)
        wl.addWidget(split)
        self.setCentralWidget(wrap)

        self.fill_voices(online=False)
        self.run_task(lambda: tts.list_voices(self.store, online=True), self._voices_loaded)
        self.refresh_library()
        self.refresh_profiles()
        self.refresh_phrases()
        self._update_engine_status()
        QTimer.singleShot(250, self.refresh_devices)

    # ---- utilità ----------------------------------------------------------
    def run_task(self, fn, on_done=None, on_fail=None) -> None:
        task = Task(fn)
        self._tasks.add(task)

        def finish() -> None:
            self._tasks.discard(task)

        def ok(result) -> None:
            finish()
            if on_done:
                on_done(result)

        def fail(msg: str) -> None:
            finish()
            if on_fail:
                on_fail(msg)
            else:
                self.msg(msg, error=True)

        task.signals.done.connect(ok)
        task.signals.failed.connect(fail)
        self.pool.start(task)

    def msg(self, text: str, error: bool = False) -> None:
        self.statusBar().setStyleSheet("color: #ff8aa1;" if error else "color: #8a94a8;")
        self.statusBar().showMessage(text, 12000)

    def _set_busy(self, delta: int) -> None:
        self._busy += delta
        busy = self._busy > 0
        for b in (self.speak_btn, self.play_btn):
            b.setEnabled(not busy)

    # ---- dispositivi ------------------------------------------------------
    def refresh_devices(self) -> None:
        self.panel.refresh_btn.setEnabled(False)
        self.panel.info.setText("Cerco i dispositivi…")
        self.run_task(discover, self._devices_found, self._devices_failed)

    def _devices_failed(self, msg: str) -> None:
        self.panel.refresh_btn.setEnabled(True)
        self.panel.info.setText("Ricerca non riuscita")
        self.msg(msg, error=True)

    def _devices_found(self, devices: list[Device]) -> None:
        self.panel.refresh_btn.setEnabled(True)
        self.panel.set_devices(devices, self.settings.get("devices"))
        for d in devices:
            self.run_task(lambda d=d: (d.uuid, self.player.get_volume(d)), self._volume_loaded, lambda _m: None)

    def _volume_loaded(self, result) -> None:
        dev_uuid, vol = result
        row = self.panel.rows.get(dev_uuid)
        if row:
            row.set_volume(vol)

    def _save_selection(self) -> None:
        self.settings.set("devices", [d.uuid for d in self.panel.checked()])

    def _set_volume(self, device: Device, value: int) -> None:
        self.run_task(lambda: self.player.set_volume(device, value),
                      lambda _r: self.msg(f"{device.name}: volume {value}%"))

    def _targets(self) -> list[Device] | None:
        devices = self.panel.checked()
        if not devices:
            self.msg("Spunta almeno un dispositivo a sinistra.", error=True)
            return None
        return devices

    def _report(self, devices: list[Device], errors: list[str], what: str) -> None:
        if errors:
            self.msg("Errore: " + " | ".join(errors), error=True)
        else:
            names = ", ".join(d.name for d in devices)
            self.msg(f"{what} → {names}")

    # ---- scheda Parla -----------------------------------------------------
    def _build_speak_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.addWidget(self._label("Cosa vuoi far dire?", "title"))
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Scrivi qui il messaggio… (Ctrl+Invio per inviarlo)")
        self.text.setFixedHeight(120)
        lay.addWidget(self.text)
        row = QHBoxLayout()
        row.addWidget(QLabel("Voce"))
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(280)
        self.voice_combo.currentIndexChanged.connect(self._voice_changed)
        row.addWidget(self.voice_combo, 1)
        row.addWidget(QLabel("Velocità"))
        self.rate = QSlider(Qt.Horizontal)
        self.rate.setRange(-50, 50)
        self.rate.setValue(int(self.settings.get("rate")))
        self.rate.setFixedWidth(130)
        self.rate_label = QLabel(f"{self.rate.value():+d}%")
        self.rate_label.setFixedWidth(42)
        self.rate.valueChanged.connect(self._rate_changed)
        row.addWidget(self.rate)
        row.addWidget(self.rate_label)
        lay.addLayout(row)
        btns = QHBoxLayout()
        self.speak_btn = QPushButton("▶  Parla sui dispositivi selezionati")
        self.speak_btn.setObjectName("accent")
        self.speak_btn.clicked.connect(self.speak)
        save = QPushButton("★ Salva frase")
        save.clicked.connect(self.save_phrase)
        stop = QPushButton("■ Ferma")
        stop.clicked.connect(self.stop_all)
        btns.addWidget(self.speak_btn, 1)
        btns.addWidget(save)
        btns.addWidget(stop)
        lay.addLayout(btns)
        lay.addWidget(self._label("Frasi rapide (doppio clic per dirle)", "title"))
        self.phrase_list = QListWidget()
        self.phrase_list.itemDoubleClicked.connect(self._phrase_activated)
        lay.addWidget(self.phrase_list, 1)
        rm = QPushButton("Elimina frase selezionata")
        rm.clicked.connect(self.delete_phrase)
        lay.addWidget(rm, 0, Qt.AlignRight)
        QShortcut(QKeySequence("Ctrl+Return"), self.text, activated=self.speak)
        return w

    @staticmethod
    def _label(text: str, name: str = "") -> QLabel:
        lab = QLabel(text)
        if name:
            lab.setObjectName(name)
        lab.setWordWrap(True)
        return lab

    def fill_voices(self, online: bool) -> None:
        self.voices = tts.list_voices(self.store, online=online)
        self._populate_voice_combo()

    def _voices_loaded(self, voices: list[tts.Voice]) -> None:
        self.voices = voices
        self._populate_voice_combo()

    def _populate_voice_combo(self) -> None:
        wanted = self.voice_combo.currentData() or self.settings.get("voice")
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        for v in self.voices:
            self.voice_combo.addItem(v.label, v.id)
        idx = self.voice_combo.findData(wanted)
        self.voice_combo.setCurrentIndex(max(0, idx))
        self.voice_combo.blockSignals(False)

    def _voice_changed(self) -> None:
        if self.voice_combo.currentData():
            self.settings.set("voice", self.voice_combo.currentData())

    def _rate_changed(self, value: int) -> None:
        self.rate_label.setText(f"{value:+d}%")
        self.settings.set("rate", value)

    def speak(self, text: str | None = None, voice_id: str | None = None) -> None:
        text = (text if isinstance(text, str) else self.text.toPlainText()).strip()
        if not text:
            self.msg("Scrivi prima un messaggio.", error=True)
            return
        devices = self._targets()
        if not devices:
            return
        voice_id = voice_id or self.voice_combo.currentData()
        rate = self.rate.value()
        lang = self.settings.get("clone_lang") or "it"
        self._set_busy(1)
        self.msg("Preparo la voce…")

        def job():
            audio = tts.synthesize(voice_id, text, self.store, self.clone, rate, lang)
            audio = prepare_audio(audio)
            return self.player.play_many(devices, audio, title="CastVoice")

        def done(errors):
            self._set_busy(-1)
            self._report(devices, errors, "Messaggio inviato")

        def fail(msg):
            self._set_busy(-1)
            self.msg(msg, error=True)

        self.run_task(job, done, fail)

    def save_phrase(self) -> None:
        text = self.text.toPlainText().strip()
        if not text:
            return
        phrases = self.settings.get("phrases")
        phrases.append({"text": text, "voice": self.voice_combo.currentData()})
        self.settings.set("phrases", phrases)
        self.refresh_phrases()

    def refresh_phrases(self) -> None:
        self.phrase_list.clear()
        for p in self.settings.get("phrases"):
            item = QListWidgetItem("★ " + p["text"].replace("\n", " ")[:90])
            item.setToolTip(p["text"])
            self.phrase_list.addItem(item)

    def _phrase_activated(self, item: QListWidgetItem) -> None:
        p = self.settings.get("phrases")[self.phrase_list.row(item)]
        self.text.setPlainText(p["text"])
        self.speak(p["text"], p.get("voice") or self.voice_combo.currentData())

    def delete_phrase(self) -> None:
        row = self.phrase_list.currentRow()
        if row >= 0:
            phrases = self.settings.get("phrases")
            del phrases[row]
            self.settings.set("phrases", phrases)
            self.refresh_phrases()

    def stop_all(self) -> None:
        devices = self._targets()
        if devices:
            self.run_task(lambda: self.player.stop_many(devices), lambda e: self._report(devices, e, "Fermato"))

    # ---- scheda Audio -----------------------------------------------------
    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.addWidget(self._label("I tuoi audio – trascina qui i file o usa «Aggiungi»", "title"))
        self.audio_list = DropList()
        self.audio_list.files_dropped.connect(self.add_files)
        self.audio_list.itemDoubleClicked.connect(lambda _i: self.play_selected())
        lay.addWidget(self.audio_list, 1)
        row = QHBoxLayout()
        add = QPushButton("＋ Aggiungi file")
        add.clicked.connect(self.pick_files)
        rec = QPushButton("● Registra clip")
        rec.clicked.connect(self.record_clip)
        self.play_btn = QPushButton("▶ Riproduci")
        self.play_btn.setObjectName("accent")
        self.play_btn.clicked.connect(self.play_selected)
        stop = QPushButton("■ Ferma")
        stop.clicked.connect(self.stop_all)
        rm = QPushButton("Rimuovi")
        rm.setObjectName("danger")
        rm.clicked.connect(self.remove_selected)
        for b in (add, rec):
            row.addWidget(b)
        row.addStretch(1)
        for b in (self.play_btn, stop, rm):
            row.addWidget(b)
        lay.addLayout(row)
        lay.addWidget(self._label("Le clip registrate restano private, su questo computer.", "muted"))
        return w

    def refresh_library(self) -> None:
        self.audio_list.clear()
        for it in self.library.items():
            icon = "🎙" if it.kind == "clip" else "♪"
            label = f"{icon}  {it.name}" + ("" if it.exists else "   (file mancante)")
            li = QListWidgetItem(label)
            li.setData(Qt.UserRole, it.id)
            li.setToolTip(it.path if it.kind == "file" else "Clip registrata")
            if not it.exists:
                li.setForeground(QColor("#ff8aa1"))
            self.audio_list.addItem(li)

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Scegli audio", str(Path.home()),
                                                "Audio (*.mp3 *.wav *.ogg *.opus *.flac *.m4a *.aac *.wma);;Tutti i file (*)")
        self.add_files([Path(f) for f in files])

    def add_files(self, files: list[Path]) -> None:
        for f in files:
            self.library.add_file(f)
        if files:
            self.refresh_library()

    def record_clip(self) -> None:
        dlg = RecordDialog(self, "Registra una clip", "Registra la tua voce (o un suono): potrai riprodurla sui Google quando vuoi.",
                           f"Clip {time.strftime('%d/%m %H:%M')}", self.settings)
        if dlg.exec() == QDialog.Accepted and dlg.wav:
            self.library.add_clip(dlg.name, dlg.wav)
            self.refresh_library()

    def _selected_item(self):
        li = self.audio_list.currentItem()
        return self.library.get(li.data(Qt.UserRole)) if li else None

    def play_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            self.msg("Seleziona un audio dalla lista.", error=True)
            return
        if not item.exists:
            self.msg("Il file non esiste più.", error=True)
            return
        devices = self._targets()
        if not devices:
            return
        self._set_busy(1)
        self.msg(f"Invio «{item.name}»…")

        def job():
            return self.player.play_many(devices, prepare_audio(Path(item.path)), title=item.name)

        def done(errors):
            self._set_busy(-1)
            self._report(devices, errors, f"«{item.name}» in riproduzione")

        def fail(msg):
            self._set_busy(-1)
            self.msg(msg, error=True)

        self.run_task(job, done, fail)

    def remove_selected(self) -> None:
        item = self._selected_item()
        if item is None:
            return
        extra = "\nLa clip registrata verrà cancellata." if item.kind == "clip" else "\nIl file originale non viene toccato."
        if QMessageBox.question(self, "Rimuovi", f"Rimuovere «{item.name}»?{extra}") == QMessageBox.Yes:
            self.library.remove(item.id)
            self.refresh_library()

    # ---- scheda La mia voce -----------------------------------------------
    def _build_voice_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.addWidget(self._label(
            "🔒 La tua voce resta solo su questo computer (cartella ~/.local/share/castvoice). "
            "Non viene mai caricata online né inclusa nel progetto pubblico.", "note"))
        lay.addWidget(self._label("Profili voce clonata", "title"))
        self.profile_list = QListWidget()
        self.profile_list.setMaximumHeight(130)
        lay.addWidget(self.profile_list)
        row = QHBoxLayout()
        rec = QPushButton("● Registra campione")
        rec.setObjectName("accent")
        rec.clicked.connect(self.record_sample)
        imp = QPushButton("Importa file…")
        imp.clicked.connect(self.import_sample)
        test = QPushButton("▶ Prova la voce")
        test.clicked.connect(self.test_profile)
        rm = QPushButton("Elimina")
        rm.setObjectName("danger")
        rm.clicked.connect(self.delete_profile)
        for b in (rec, imp, test):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(rm)
        lay.addLayout(row)
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Lingua della voce clonata"))
        self.lang_combo = QComboBox()
        for code, name in (("it", "Italiano"), ("en", "English"), ("es", "Español"), ("fr", "Français"),
                           ("de", "Deutsch"), ("pt", "Português")):
            self.lang_combo.addItem(name, code)
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(self.settings.get("clone_lang") or "it")))
        self.lang_combo.currentIndexChanged.connect(lambda _i: self.settings.set("clone_lang", self.lang_combo.currentData()))
        lang_row.addWidget(self.lang_combo)
        lang_row.addStretch(1)
        lay.addLayout(lang_row)
        lay.addWidget(self._label("Motore di clonazione (XTTS v2, gira sulla tua GPU)", "title"))
        self.engine_status = self._label("", "muted")
        lay.addWidget(self.engine_status)
        self.license_check = QCheckBox(
            "Accetto la licenza non commerciale del modello XTTS v2 (Coqui Public Model License, "
            "coqui.ai/cpml) per uso personale")
        self.license_check.setChecked(bool(self.settings.get("xtts_license_accepted")))
        self.license_check.toggled.connect(self._license_toggled)
        lay.addWidget(self.license_check)
        self.install_btn = QPushButton("⬇ Installa il motore di clonazione")
        self.install_btn.clicked.connect(self.install_engine)
        lay.addWidget(self.install_btn, 0, Qt.AlignLeft)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        lay.addWidget(self.progress)
        lay.addStretch(1)
        return w

    def _license_toggled(self, value: bool) -> None:
        self.settings.set("xtts_license_accepted", value)
        self._update_engine_status()

    def _update_engine_status(self) -> None:
        if not tts.CloneEngine.installed():
            text = "Non installato. Serve una volta sola (qualche GB di download)."
            self.install_btn.show()
        elif not self.settings.get("xtts_license_accepted"):
            text = "Installato. Accetta la licenza qui sotto per poterlo usare."
            self.install_btn.hide()
        else:
            text = "Pronto. Il modello (~1,8 GB) si scarica automaticamente al primo uso."
            self.install_btn.hide()
        self.engine_status.setText(text)

    def install_engine(self) -> None:
        if self.install_proc is not None:
            return
        self.install_btn.setEnabled(False)
        self.progress.show()
        self.engine_status.setText("Installazione in corso… può richiedere diversi minuti.")
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyRead.connect(lambda: self._install_output(proc))
        proc.finished.connect(lambda code, _s: self._install_done(code))
        self.install_proc = proc
        proc.start("bash", ["-lc", tts.CLONE_INSTALL_CMD])

    def _install_output(self, proc: QProcess) -> None:
        lines = bytes(proc.readAll()).decode(errors="replace").strip().splitlines()
        if lines:
            self.engine_status.setText("Installazione: " + lines[-1][:110])

    def _install_done(self, code: int) -> None:
        self.install_proc = None
        self.progress.hide()
        self.install_btn.setEnabled(True)
        self._update_engine_status()
        if code != 0:
            self.engine_status.setText("Installazione non riuscita (serve `uv` e connessione a internet).")

    def refresh_profiles(self) -> None:
        self.profile_list.clear()
        for p in self.store.profiles():
            li = QListWidgetItem(f"🎙  {p.name}")
            li.setData(Qt.UserRole, p.slug)
            self.profile_list.addItem(li)
        self.fill_voices(online=False) if not self.voices else None

    def _profiles_changed(self) -> None:
        self.refresh_profiles()
        self.run_task(lambda: tts.list_voices(self.store, online=True), self._voices_loaded)

    def record_sample(self) -> None:
        dlg = RecordDialog(self, "Registra un campione della tua voce",
                           "Leggi ad alta voce, con calma, per 15-30 secondi, in un posto silenzioso:\n\n" + SAMPLE_TEXT,
                           "La mia voce", self.settings)
        if dlg.exec() == QDialog.Accepted and dlg.wav:
            try:
                self.store.add(dlg.name, dlg.wav)
            finally:
                dlg.wav.unlink(missing_ok=True)
            self._profiles_changed()

    def import_sample(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Campione della voce (10-30 secondi, pulito)", str(Path.home()),
                                           "Audio (*.wav *.mp3 *.ogg *.flac *.m4a)")
        if not f:
            return
        name, ok = QInputDialog.getText(self, "Nome del profilo", "Come lo chiamo?", text="La mia voce")
        if ok and name.strip():
            self.store.add(name.strip(), Path(f))  # copia: l'originale non viene toccato
            self._profiles_changed()

    def _selected_profile(self):
        li = self.profile_list.currentItem()
        return next((p for p in self.store.profiles() if li and p.slug == li.data(Qt.UserRole)), None)

    def test_profile(self) -> None:
        p = self._selected_profile()
        if p is None:
            self.msg("Seleziona un profilo voce.", error=True)
            return
        self.speak("Ciao! Questa è la mia voce clonata, parlo dal tuo Google.", f"clone:{p.slug}")

    def delete_profile(self) -> None:
        p = self._selected_profile()
        if p and QMessageBox.question(self, "Elimina", f"Eliminare il profilo «{p.name}» e il suo campione?") == QMessageBox.Yes:
            self.store.remove(p.slug)
            self._profiles_changed()

    # ---- chiusura ---------------------------------------------------------
    def closeEvent(self, event) -> None:
        if self.install_proc is not None:
            self.install_proc.kill()
        self.clone.close()
        self.player.close()
        event.accept()


def run() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    app.setApplicationName("CastVoice")
    app.setDesktopFileName("castvoice")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    icon = paths.ASSETS_DIR / "castvoice.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    return app.exec()
