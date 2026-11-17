"""Interfaccia grafica di CastVoice (PySide6)."""
from __future__ import annotations

import locale
import logging
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSplitter, QTabWidget,
    QVBoxLayout, QWidget,
)

from . import paths, tts
from .engine import Device, Player, discover, prepare_audio
from .library import Library, Recorder, Settings, VoiceStore, audio_sources
from .script_edit import ScriptEdit, voice_color

log = logging.getLogger("castvoice")

PURE_AUDIO_EXT = {".mp3", ".wav", ".ogg", ".opus", ".flac", ".m4a", ".aac", ".wma"}
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".wmv", ".flv", ".mpg", ".mpeg", ".3gp", ".ts"}
AUDIO_EXT = PURE_AUDIO_EXT | VIDEO_EXT  # dai video si usa solo la traccia audio
FILE_FILTER = (
    "Audio e video (" + " ".join(f"*{e}" for e in sorted(AUDIO_EXT)) + ");;"
    "Solo audio (" + " ".join(f"*{e}" for e in sorted(PURE_AUDIO_EXT)) + ");;"
    "Solo video (" + " ".join(f"*{e}" for e in sorted(VIDEO_EXT)) + ");;Tutti i file (*)"
)
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


class _StageSignal(QObject):
    """Fasi della preparazione della voce, emesse dai thread di lavoro verso l'interfaccia."""
    stage = Signal(str)  # "load" | "synth" | "send" | "play"


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
            self._emit(self.signals.failed, f"{type(exc).__name__}: {exc}")
        else:
            self._emit(self.signals.done, result)

    @staticmethod
    def _emit(signal, value) -> None:
        try:
            signal.emit(value)
        except RuntimeError:
            pass  # la finestra è già stata chiusa: niente da aggiornare


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


# --------------------------------------------------------------------------
# Selettore voci
# --------------------------------------------------------------------------
ENGINE_BADGE = {"clone": "🎙", "espeak": "💻", "edge": "🌐"}
ENGINE_RANK = {"clone": 0, "edge": 1, "espeak": 2}
ENGINE_WORDS = {"clone": "mia voce clonata", "espeak": "locale offline", "edge": "online naturale"}


class VoicePicker(QWidget):
    """Scelta della voce a livelli: tipo → lingua → ricerca → elenco, con preferite e anteprima."""

    changed = Signal(str)
    preview_requested = Signal(str)

    TYPES = [("all", "Tutte le voci"), ("fav", "★ Preferite"), ("clone", "🎙 La mia voce"),
             ("espeak", "💻 Locali (senza internet)"), ("edge", "🌐 Online (naturali)")]

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.voices: list[tts.Voice] = []
        self._current: str = settings.get("voice") or ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        filt = QHBoxLayout()
        self.type_combo = QComboBox()
        for key, label in self.TYPES:
            self.type_combo.addItem(label, key)
        self.lang_combo = QComboBox()
        self.search = QLineEdit()
        self.search.setPlaceholderText("🔍 Cerca: nome, uomo, donna…")
        self.search.setClearButtonEnabled(True)
        filt.addWidget(self.type_combo, 2)
        filt.addWidget(self.lang_combo, 2)
        filt.addWidget(self.search, 3)
        lay.addLayout(filt)

        self.list = QListWidget()
        self.list.setMinimumHeight(120)
        lay.addWidget(self.list, 1)

        bottom = QHBoxLayout()
        self.chosen = QLabel()
        self.chosen.setStyleSheet("color: #22d3ee; font-weight: 700;")
        self.chosen.setWordWrap(True)
        self.count = QLabel()
        self.count.setObjectName("muted")
        self.fav_btn = QPushButton("☆ Preferita")
        self.prev_btn = QPushButton("🔊 Anteprima")
        self.prev_btn.setToolTip("Ascolta la voce sul PC (anche doppio clic sulla voce)")
        bottom.addWidget(self.chosen, 1)
        bottom.addWidget(self.count)
        bottom.addWidget(self.fav_btn)
        bottom.addWidget(self.prev_btn)
        lay.addLayout(bottom)

        self.type_combo.currentIndexChanged.connect(self._refilter)
        self.lang_combo.currentIndexChanged.connect(self._refilter)
        self.search.textChanged.connect(self._refilter)
        self.list.currentItemChanged.connect(self._picked)
        self.list.itemDoubleClicked.connect(lambda _i: self._preview())
        self.fav_btn.clicked.connect(self._toggle_fav)
        self.prev_btn.clicked.connect(self._preview)

    # ---- API ----------------------------------------------------------------
    def current_id(self) -> str:
        return self._current

    def set_voices(self, voices: list[tts.Voice]) -> None:
        self.voices = voices
        cur = self._voice(self._current)
        langs = sorted({v.lang for v in voices if v.lang}, key=lambda c: (c != "it", tts.LANG_NAMES.get(c, c)))
        previous = self.lang_combo.currentData()  # None alla prima volta
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        self.lang_combo.addItem("Tutte le lingue", "")
        for code in langs:
            self.lang_combo.addItem(tts.LANG_NAMES.get(code, code), code)
        wanted = previous if previous is not None else (cur.lang if cur and cur.lang else "it")
        self.lang_combo.setCurrentIndex(max(0, self.lang_combo.findData(wanted)))
        self.lang_combo.blockSignals(False)
        self._refilter()
        self._update_chosen()

    # ---- interno ------------------------------------------------------------
    def _voice(self, voice_id: str) -> tts.Voice | None:
        return next((v for v in self.voices if v.id == voice_id), None)

    def _favs(self) -> list[str]:
        return list(self.settings.get("favorite_voices") or [])

    @staticmethod
    def _matches(v: tts.Voice, kind: str, lang: str, tokens: list[str], favs: set[str]) -> bool:
        if kind == "fav" and v.id not in favs:
            return False
        if kind in ("clone", "espeak", "edge") and v.engine != kind:
            return False
        if lang and v.lang and v.lang != lang:  # le voci senza lingua (clonate) restano sempre
            return False
        hay = f"{v.name} {v.gender} {v.locale} {tts.LANG_NAMES.get(v.lang, '')} {ENGINE_WORDS.get(v.engine, '')}".lower()
        return all(t in hay for t in tokens)

    @staticmethod
    def _item_text(v: tts.Voice, fav: bool) -> str:
        sesso = f" · {v.gender}" if v.gender else ""
        where = {"clone": "tua voce clonata", "espeak": "locale", "edge": f"online · {v.locale}"}.get(v.engine, "")
        return f"{ENGINE_BADGE.get(v.engine, '')}  {'★ ' if fav else ''}{v.name}{sesso}  ·  {where}"

    def _refilter(self, *_args) -> None:
        kind = self.type_combo.currentData()
        lang = self.lang_combo.currentData() or ""
        tokens = self.search.text().lower().split()
        favs = set(self._favs())
        shown = [v for v in self.voices if self._matches(v, kind, lang, tokens, favs)]
        shown.sort(key=lambda v: (v.id not in favs, ENGINE_RANK.get(v.engine, 9),
                                  v.lang not in ("it", ""), v.locale, v.name.lower()))  # italiano per primo
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for v in shown:
            item = QListWidgetItem(self._item_text(v, v.id in favs))
            item.setData(Qt.UserRole, v.id)
            item.setToolTip(v.label)
            self.list.addItem(item)
            if v.id == self._current:
                target = item
        if not shown:
            hint = {"clone": "Nessuna voce clonata: creala nella scheda «La mia voce».",
                    "fav": "Nessuna preferita: scegli una voce e premi «☆ Preferita»."}.get(
                        kind, "Nessuna voce con questi filtri.")
            item = QListWidgetItem(hint)
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        if target is not None:
            self.list.setCurrentItem(target)
            self.list.scrollToItem(target)
        self.list.blockSignals(False)
        self.count.setText(f"{len(shown)} voci")

    def _picked(self, item, _previous=None) -> None:
        voice_id = item.data(Qt.UserRole) if item else None
        if not voice_id:
            return
        self._current = voice_id
        self._update_chosen()
        self.changed.emit(voice_id)

    def _update_chosen(self) -> None:
        v = self._voice(self._current)
        if v is None:
            self.chosen.setText("Scegli una voce dall'elenco")
            self.fav_btn.setText("☆ Preferita")
            return
        extra = " · ".join(x for x in (v.gender, v.locale or tts.LANG_NAMES.get(v.lang, "")) if x)
        self.chosen.setText(f"Voce scelta: {v.name}" + (f" ({extra})" if extra else "") + f" · {ENGINE_WORDS.get(v.engine, '')}")
        self.fav_btn.setText("★ Preferita" if self._current in self._favs() else "☆ Preferita")

    def _toggle_fav(self) -> None:
        if not self._current:
            return
        favs = self._favs()
        if self._current in favs:
            favs.remove(self._current)
        else:
            favs.append(self._current)
        self.settings.set("favorite_voices", favs)
        self._refilter()
        self._update_chosen()

    def _preview(self) -> None:
        if self._current:
            self.preview_requested.emit(self._current)


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
        self.resize(1100, 780)
        self.settings = Settings()
        self.library = Library()
        self.store = VoiceStore()
        self.player = Player()
        self.clone = tts.CloneEngine(lambda: bool(self.settings.get("xtts_license_accepted")),
                                     lambda: self.settings.get("clone_engine"))
        self.pool = QThreadPool.globalInstance()
        self._tasks: set[Task] = set()
        self.voices: list[tts.Voice] = []
        self.install_proc: QProcess | None = None
        self._busy = 0
        self._preview_proc: subprocess.Popen | None = None
        self._slot_buttons: list = []  # [(id voce, pulsante)] delle scorciatoie Alt+numero
        self._synth_busy = False   # una sola preparazione di voce alla volta (niente clic accumulati)
        self._speak_gen = 0        # cambia quando si annulla: il lavoro in corso non parla più
        self._prog: dict | None = None
        self._stage_sig = _StageSignal()
        self._stage_sig.stage.connect(self._on_stage)
        self.clone.on_stage = self._stage_sig.stage.emit
        self._prog_timer = QTimer(self)
        self._prog_timer.setInterval(100)
        self._prog_timer.timeout.connect(self._tick_progress)

        self.panel = DevicePanel()
        self.panel.refresh_requested.connect(self.refresh_devices)
        self.panel.selection_changed.connect(self._save_selection)
        self.panel.volume_changed.connect(self._set_volume)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_speak_tab(), "💬  Parla")
        self.tabs.addTab(self._build_audio_tab(), "🎵  Audio")
        self.tabs.addTab(self._build_voice_tab(), "🎙  La mia voce")
        self.tabs.currentChanged.connect(lambda _i: self._rebuild_slots())  # così le ★ preferite sono aggiornate

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
        self.text = ScriptEdit()
        self.text.setPlaceholderText(
            "Scrivi qui il messaggio… (Ctrl+Invio per inviarlo). Per far parlare più voci nello stesso "
            "messaggio scegli una voce qui sotto (o Alt+1, Alt+2…) e continua a scrivere.")
        self.text.setFixedHeight(110)
        self.text.voice_changed.connect(self._update_chips)
        lay.addWidget(self.text)
        self.slot_grid = QGridLayout()
        self.slot_grid.setSpacing(6)
        lay.addLayout(self.slot_grid)
        self.voice_picker = VoicePicker(self.settings)
        lay.addWidget(self._label("Scegli la voce", "title"))
        self.voice_picker.changed.connect(self._voice_changed)
        self.voice_picker.preview_requested.connect(self.preview_voice)
        lay.addWidget(self.voice_picker, 3)
        row = QHBoxLayout()
        row.addStretch(1)
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
        self.progress_label = self._label("", "muted")
        self.progress_label.hide()
        lay.addWidget(self.progress_label)
        self.speak_progress = QProgressBar()
        self.speak_progress.setRange(0, 100)
        self.speak_progress.setFormat("%p%")
        self.speak_progress.setToolTip("Percentuale stimata in base ai tempi delle volte precedenti")
        self.speak_progress.hide()
        lay.addWidget(self.speak_progress)
        lay.addWidget(self._label("Frasi rapide (doppio clic per dirle)", "title"))
        self.phrase_list = QListWidget()
        self.phrase_list.itemDoubleClicked.connect(self._phrase_activated)
        self.phrase_list.setMaximumHeight(96)
        lay.addWidget(self.phrase_list)
        rm = QPushButton("Elimina frase selezionata")
        rm.clicked.connect(self.delete_phrase)
        lay.addWidget(rm, 0, Qt.AlignRight)
        QShortcut(QKeySequence("Ctrl+Return"), self.text, activated=self.speak)
        for n in range(1, 10):  # Alt+1…Alt+9: cambia voce al volo
            QShortcut(QKeySequence(f"Alt+{n}"), self, activated=lambda n=n: self.use_slot(n - 1))
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
        self.voice_picker.set_voices(self.voices)
        self._rebuild_slots()

    def _voice_changed(self, voice_id: str) -> None:
        self.settings.set("voice", voice_id)
        self.text.set_voice(voice_id, to_selection=False)  # scegliendo dalla lista si scrive con questa voce
        self._update_chips(voice_id)

    # ---- voci su Alt+1…Alt+9 ----------------------------------------------
    def _slot_ids(self) -> list[str]:
        explicit = self.settings.get("voice_slots")
        if explicit is None:  # automatico: prima le voci clonate, poi le preferite
            ids = [f"clone:{p.slug}" for p in self.store.profiles()] + list(self.settings.get("favorite_voices") or [])
        else:
            ids = list(explicit)
        clones = {p.slug for p in self.store.profiles()}
        out: list[str] = []
        for v in ids:
            if v and v not in out and not (v.startswith("clone:") and v[6:] not in clones):
                out.append(v)
        return out[:9]

    def _voice_name(self, voice_id: str) -> str:
        v = next((v for v in self.voices if v.id == voice_id), None)
        if v is None:
            return voice_id.split(":", 1)[-1][:20]
        name = v.name if v.engine == "clone" and v.name else v.label
        return name if len(name) <= 20 else name[:19] + "…"

    def _rebuild_slots(self) -> None:
        while self.slot_grid.count():
            item = self.slot_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        ids = self._slot_ids()
        self._slot_buttons = []
        for i, vid in enumerate(ids):
            col = voice_color(vid, ids)
            b = QPushButton(f"Alt+{i + 1}  {self._voice_name(vid)}")
            b.setCheckable(True)
            b.setStyleSheet(
                f"QPushButton {{ border: 2px solid {col}; padding: 5px 10px; }}"
                f"QPushButton:checked {{ background: {col}; color: #06222a; font-weight: 700; }}")
            b.setToolTip(f"Clic o Alt+{i + 1}: scrivi con questa voce. Tasto destro: toglila.")
            b.clicked.connect(lambda _c=False, i=i: self.use_slot(i))
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda pos, i=i, b=b: self._slot_menu(i, b.mapToGlobal(pos)))
            self.slot_grid.addWidget(b, i // 3, i % 3)
            self._slot_buttons.append((vid, b))
        if len(ids) < 9:
            add = QPushButton("＋ Aggiungi la voce scelta")
            add.setToolTip("Mette sulla prossima scorciatoia Alt+numero la voce selezionata nella lista qui sotto.")
            add.clicked.connect(self._add_slot)
            self.slot_grid.addWidget(add, len(ids) // 3, len(ids) % 3)
        if ids != self.text.order:  # i colori seguono la posizione: riapplicali solo se è cambiata
            self.text.order = ids
            self.text.recolor()
        self._update_chips(self.text.current_voice())

    def _update_chips(self, voice_id: str = "") -> None:
        for vid, b in getattr(self, "_slot_buttons", []):
            b.setChecked(vid == voice_id)

    def use_slot(self, i: int) -> None:
        ids = [v for v, _b in self._slot_buttons]
        if i >= len(ids):
            self.msg(f"Alt+{i + 1}: nessuna voce assegnata. Usa «＋ Aggiungi la voce scelta».", error=True)
            return
        self.tabs.setCurrentIndex(0)  # scheda Parla
        self.text.set_voice(ids[i])
        self._update_chips(ids[i])
        self.text.setFocus()
        self.msg(f"Da qui scrivi con la voce: {self._voice_name(ids[i])}")

    def _add_slot(self) -> None:
        vid, ids = self.voice_picker.current_id(), self._slot_ids()
        if not vid or vid in ids:
            self.msg("Questa voce è già tra le scorciatoie (o non ne hai scelta una).", error=True)
            return
        self.settings.set("voice_slots", ids + [vid])
        self._rebuild_slots()

    def _slot_menu(self, i: int, pos) -> None:
        menu = QMenu(self)
        remove = menu.addAction("Togli dalle scorciatoie")
        if menu.exec(pos) is remove:
            ids = self._slot_ids()
            del ids[i]
            self.settings.set("voice_slots", ids)
            self._rebuild_slots()

    # ---- avanzamento della preparazione della voce ------------------------
    _STAGE_TEXT = {
        "load": "Carico il modello vocale (ci mette un po', poi resta pronto)…",
        "send": "Invio ai dispositivi…",
        "play": "Riproduco l'anteprima…",
    }
    _BUSY_MSG = "Sto ancora preparando la voce: aspetta che finisca (o premi ■ Ferma)."

    def _stage_label(self, name: str, parts: int) -> str:
        if name.startswith("part"):
            i = int(name[4:])
            return f"Genero la voce… (parte {i + 1} di {parts})" if parts > 1 else "Genero la voce…"
        return self._STAGE_TEXT[name]

    def _begin_synth(self, segs: list[tuple[str, str]]) -> dict:
        """Segna l'inizio di una preparazione e mostra la barra. Ritorna lo stato della richiesta."""
        self._synth_busy = True
        self._speak_gen += 1
        timing = self.settings.get("timing") or {}
        per_char = float(timing.get("synth_per_char", 0.1))
        plan: list[list] = []  # [fase, secondi previsti]
        if any(v.startswith("clone:") for v, _t in segs) and self.clone.needs_load():
            plan.append(["load", float(timing.get("load", 45.0))])
        for i, (v, t) in enumerate(segs):
            plan.append([f"part{i}", max(2.0, per_char * len(t)) if v.startswith("clone:") else 2.5])
        plan.append(["send", 2.0])
        self._prog = {"gen": self._speak_gen, "plan": plan, "idx": 0, "t": time.monotonic(), "parts": len(segs),
                      "load": 0.0, "clone_secs": 0.0, "clone_chars": 0, "cancelled": False}
        self.speak_progress.setRange(0, 100)
        self.speak_progress.setValue(0)
        self.speak_progress.show()
        self.progress_label.show()
        self._tick_progress()
        self._prog_timer.start()
        return self._prog

    def _synth_parts(self, prog: dict, gen: int, segs: list[tuple[str, str]], rate: int, lang: str) -> Path | None:
        """Fa dire ogni pezzo con la sua voce e li unisce in un unico audio. None = annullato con ■ Ferma."""
        parts = []
        for i, (voice_id, text) in enumerate(segs):
            self._stage_sig.stage.emit(f"part{i}")
            t0 = time.monotonic()
            parts.append(tts.synthesize(voice_id, text, self.store, self.clone, rate, lang))
            if voice_id.startswith("clone:"):  # tempi veri, per stimare meglio la barra la prossima volta
                load = self.clone.timing.pop("load", 0.0)
                prog["load"] += load
                prog["clone_secs"] += time.monotonic() - t0 - load
                prog["clone_chars"] += len(text)
            if gen != self._speak_gen:
                return None
        return tts.join_audio(parts)

    def _on_stage(self, name: str) -> None:
        p = self._prog
        if p is None or p["cancelled"]:
            return
        if name == "play":  # anteprima: la voce è pronta e sta suonando
            self._finish_progress(p["gen"])
            return
        names = [s[0] for s in p["plan"]]
        if name == "load" and "load" not in names:  # il modello si è dovuto ricaricare all'improvviso
            p["plan"].insert(0, ["load", float((self.settings.get("timing") or {}).get("load", 45.0))])
            names.insert(0, "load")
        if name in names:
            p["idx"] = names.index(name)
            p["t"] = time.monotonic()

    def _tick_progress(self) -> None:
        """Percentuale stimata: tempo trascorso rispetto ai tempi misurati le volte precedenti."""
        p = self._prog
        if p is None or p["cancelled"]:
            return
        plan, idx = p["plan"], p["idx"]
        name, expected = plan[idx]
        total = sum(s[1] for s in plan)
        before = sum(s[1] for s in plan[:idx])
        elapsed = time.monotonic() - p["t"]
        # se ci mette più del previsto la barra rallenta senza mai arrivare a 100 prima della fine vera
        frac = elapsed / expected if elapsed < 0.9 * expected else 0.9 + 0.1 * (1 - math.exp(-(elapsed - 0.9 * expected) / expected))
        pct = min(99, int((before + frac * expected) / total * 100))
        self.speak_progress.setValue(max(self.speak_progress.value(), pct))  # mai all'indietro
        self.progress_label.setText(self._stage_label(name, p["parts"]))

    def _learn_timing(self, p: dict) -> None:
        """Salva i tempi veri di questa volta per rendere più precisa la percentuale della prossima."""
        t = dict(self.settings.get("timing") or {})
        self.clone.timing = {}

        def blend(key: str, new: float) -> None:
            t[key] = round(0.5 * t[key] + 0.5 * new, 2) if key in t else round(new, 2)

        if p["load"] > 0:
            blend("load", p["load"])
        if p["clone_chars"] and p["clone_secs"] > 0:
            blend("synth_per_char", p["clone_secs"] / p["clone_chars"])
        self.settings.set("timing", t)

    def _finish_progress(self, gen: int, learn: bool = True) -> None:
        """Chiude la barra: preparazione finita, annullata o fallita. Si può chiamare più volte."""
        p = self._prog
        if p is None or p["gen"] != gen:
            return
        self._prog_timer.stop()
        self._prog = None
        self._synth_busy = False
        if learn:
            self._learn_timing(p)
        self.speak_progress.setRange(0, 100)
        if p["cancelled"] or not learn:
            self._hide_progress()
        else:
            self.speak_progress.setValue(100)
            QTimer.singleShot(700, self._hide_progress)

    def _hide_progress(self) -> None:
        if self._prog is None:  # se nel frattempo è partita un'altra richiesta, la barra resta
            self.speak_progress.hide()
            self.progress_label.hide()

    def _cancel_synth(self) -> bool:
        """■ Ferma durante la preparazione: quando finisce di caricare non parla più."""
        p = self._prog
        if not self._synth_busy or p is None or p["cancelled"]:
            return False
        p["cancelled"] = True
        self._speak_gen += 1
        self._prog_timer.stop()
        self.speak_progress.setRange(0, 0)  # animazione: aspetto solo che il caricamento termini
        self.progress_label.setText("Annullato: aspetto che finisca il caricamento, poi non parlerà.")
        return True

    def preview_voice(self, voice_id: str) -> None:
        """Fa sentire la voce sul PC (non sui Google), così si sceglie senza disturbare nessuno."""
        if self._synth_busy:
            self.msg(self._BUSY_MSG, error=True)
            return
        voice = next((v for v in self.voices if v.id == voice_id), None)
        lang = voice.lang if voice and voice.lang else (self.settings.get("clone_lang") or "it")
        text = tts.SAMPLE_PHRASES.get(lang, tts.SAMPLE_PHRASES["en"])
        rate = self.rate.value()
        segs = [(voice_id, text)]
        prog = self._begin_synth(segs)
        gen = prog["gen"]
        self.msg("Preparo l'anteprima…")

        def job():
            audio = self._synth_parts(prog, gen, segs, rate, lang)
            if audio is None:  # annullata con ■ Ferma mentre si preparava
                return False
            self._stage_sig.stage.emit("play")
            if self._preview_proc and self._preview_proc.poll() is None:
                self._preview_proc.terminate()  # una sola anteprima alla volta
            ffplay = paths.tool("ffplay")
            if shutil.which(ffplay) or Path(ffplay).exists():
                self._preview_proc = subprocess.Popen(
                    [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", str(audio)])
                self._preview_proc.wait()
            else:  # senza ffplay lo apre il lettore audio del sistema
                _open_with_system(audio)
            return True

        def done(played):
            self._finish_progress(gen)
            self.msg("Anteprima finita." if played else "Anteprima annullata.")

        def fail(msg):
            self._finish_progress(gen, learn=False)
            self.msg(msg, error=True)

        self.run_task(job, done, fail)

    def _rate_changed(self, value: int) -> None:
        self.rate_label.setText(f"{value:+d}%")
        self.settings.set("rate", value)

    def speak(self, text: str | None = None, voice_id: str | None = None) -> None:
        if self._synth_busy:  # pulsante, Ctrl+Invio e frasi rapide passano tutti da qui
            self.msg(self._BUSY_MSG, error=True)
            return
        default_voice = voice_id or self.text.current_voice() or self.voice_picker.current_id()
        if isinstance(text, str):  # chiamata diretta (es. «Prova la voce»): un solo pezzo
            segs = [(default_voice, text.strip())]
        else:  # il messaggio scritto, con le sue voci
            segs = self.text.segments(default_voice)
        segs = [(v, t) for v, t in segs if t]
        if not segs:
            self.msg("Scrivi prima un messaggio.", error=True)
            return
        devices = self._targets()
        if not devices:
            return
        rate = self.rate.value()
        lang = self.settings.get("clone_lang") or "it"
        self._set_busy(1)
        prog = self._begin_synth(segs)
        gen = prog["gen"]
        self.msg("Preparo la voce…" if len(segs) == 1 else f"Preparo la voce ({len(segs)} parti)…")

        def job():
            audio = self._synth_parts(prog, gen, segs, rate, lang)
            if audio is None:  # annullato con ■ Ferma mentre si preparava: non parla
                return None
            self._stage_sig.stage.emit("send")
            audio = prepare_audio(audio)
            return self.player.play_many(devices, audio, title="CastVoice")

        def done(errors):
            self._set_busy(-1)
            self._finish_progress(gen)
            if errors is None:
                self.msg("Annullato: non ho inviato nulla.")
            else:
                self._report(devices, errors, "Messaggio inviato")

        def fail(msg):
            self._set_busy(-1)
            self._finish_progress(gen, learn=False)
            self.msg(msg, error=True)

        self.run_task(job, done, fail)

    def save_phrase(self) -> None:
        segs = self.text.segments(self.text.current_voice() or self.voice_picker.current_id())
        if not segs:
            return
        phrases = self.settings.get("phrases")
        phrases.append({"text": " ".join(t for _v, t in segs), "voice": segs[0][0],
                        "segments": [[v, t] for v, t in segs]})  # più voci nella stessa frase
        self.settings.set("phrases", phrases)
        self.refresh_phrases()

    def refresh_phrases(self) -> None:
        self.phrase_list.clear()
        for p in self.settings.get("phrases"):
            item = QListWidgetItem("★ " + p["text"].replace("\n", " ")[:90])
            item.setToolTip(p["text"])
            self.phrase_list.addItem(item)

    def _phrase_activated(self, item: QListWidgetItem) -> None:
        if self._synth_busy:
            self.msg(self._BUSY_MSG, error=True)
            return
        p = self.settings.get("phrases")[self.phrase_list.row(item)]
        voice = p.get("voice") or self.voice_picker.current_id()
        self.text.set_segments([(v, t) for v, t in (p.get("segments") or [[voice, p["text"]]])])
        self.speak()

    def delete_phrase(self) -> None:
        row = self.phrase_list.currentRow()
        if row >= 0:
            phrases = self.settings.get("phrases")
            del phrases[row]
            self.settings.set("phrases", phrases)
            self.refresh_phrases()

    def stop_all(self) -> None:
        cancelled = self._cancel_synth()  # se sta ancora preparando la voce, la annulla
        devices = self.panel.checked() if cancelled else self._targets()
        if cancelled:
            self.msg("Annullato.")
        if devices:
            self.run_task(lambda: self.player.stop_many(devices), lambda e: self._report(devices, e, "Fermato"))

    # ---- scheda Audio -----------------------------------------------------
    def _build_audio_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.addWidget(self._label("I tuoi audio e video (dei video si sente solo l'audio) – trascina qui i file o usa «Aggiungi»", "title"))
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
            if it.kind == "clip":
                icon = "🎙"
            elif Path(it.path).suffix.lower() in VIDEO_EXT:
                icon = "🎬"  # video: verrà riprodotto solo l'audio
            else:
                icon = "♪"
            label = f"{icon}  {it.name}" + ("" if it.exists else "   (file mancante)")
            li = QListWidgetItem(label)
            li.setData(Qt.UserRole, it.id)
            li.setToolTip(it.path if it.kind == "file" else "Clip registrata")
            if not it.exists:
                li.setForeground(QColor("#ff8aa1"))
            self.audio_list.addItem(li)

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Scegli audio o video (dei video si usa solo l'audio)",
                                                str(Path.home()), FILE_FILTER)
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
            f"🔒 La tua voce resta solo su questo computer (cartella {paths.DATA_DIR}). "
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
        lay.addWidget(self._label("Modello di clonazione (gira sulla tua GPU)", "title"))
        self.engine_combo = QComboBox()
        self.engine_combo.addItem("Qwen3-TTS 1.7B · più realistico, licenza libera (Apache-2.0)", "qwen")
        self.engine_combo.addItem("XTTS v2 · Coqui, solo uso non commerciale", "xtts")
        self.engine_combo.setCurrentIndex(max(0, self.engine_combo.findData(self._clone_engine())))
        self.engine_combo.currentIndexChanged.connect(self._engine_changed)
        lay.addWidget(self.engine_combo)
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

    def _clone_engine(self) -> str:
        e = self.settings.get("clone_engine")
        return e if e in tts.CLONE_ENGINES else tts.DEFAULT_CLONE_ENGINE

    def _engine_changed(self, _index: int) -> None:
        self.settings.set("clone_engine", self.engine_combo.currentData())
        self.clone.release()  # il nuovo modello si carica al prossimo uso
        self._update_engine_status()

    def _update_engine_status(self) -> None:
        engine = self._clone_engine()
        name = tts.CLONE_ENGINES[engine][0]
        size = "~3,9 GB" if engine == "qwen" else "~1,8 GB"
        needs_license = engine == "xtts"
        self.license_check.setVisible(needs_license)
        if not tts.CloneEngine.engine_installed(engine):
            text = f"{name} non è installato. Serve una volta sola (qualche GB di download)."
            self.install_btn.setText(f"⬇ Installa {name}")
            self.install_btn.show()
        elif needs_license and not self.settings.get("xtts_license_accepted"):
            text = "Installato. Accetta la licenza qui sotto per poterlo usare."
            self.install_btn.hide()
        else:
            text = f"{name} pronto. Il modello ({size}) si scarica automaticamente al primo uso."
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
        cmd = tts.CLONE_ENGINES[self._clone_engine()][3]
        if sys.platform == "win32":
            proc.start("cmd", ["/c", cmd])
        else:
            proc.start("bash", ["-lc", cmd])

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
        self._rebuild_slots()  # le voci clonate sono le prime scorciatoie

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
        if self._preview_proc and self._preview_proc.poll() is None:
            self._preview_proc.terminate()
        self.clone.close()
        self.player.close()
        event.accept()


def _open_with_system(path: Path) -> None:
    """Apre un file con il programma predefinito del sistema."""
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)])


def selftest() -> int:
    """Prova rapida (usata dalle build automatiche): l'app si costruisce e trova ciò che le serve."""
    problems: list[str] = []
    for name in ("ffmpeg", "ffprobe"):
        try:
            subprocess.run([paths.tool(name), "-version"], capture_output=True, check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            problems.append(f"{name} non trovato")
    for f in (tts.WORKER, tts.QWEN_WORKER, paths.ASSETS_DIR / "castvoice.svg"):
        if not f.exists():
            problems.append(f"manca {f.name}")
    for mod in ("pychromecast", "zeroconf", "edge_tts"):
        try:
            __import__(mod)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{mod}: {exc}")
    app = QApplication.instance() or QApplication(sys.argv)
    win = MainWindow()
    deadline = time.time() + 3
    while time.time() < deadline:  # lascia girare l'interfaccia (timer, ricerca dispositivi)
        app.processEvents()
        time.sleep(0.02)
    win.clone.close()
    report = (f"CastVoice {__import__('castvoice').__version__} - dati: {paths.DATA_DIR}\n"
              "SELFTEST " + ("OK" if not problems else "FALLITO: " + "; ".join(problems)))
    out = os.environ.get("CASTVOICE_SELFTEST_FILE")  # su Windows l'app senza console non ha stdout
    if out:
        Path(out).write_text(report, encoding="utf-8")
    try:
        print(report)
    except (OSError, UnicodeError):
        pass
    return 0 if not problems else 1


def run() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    app = QApplication(sys.argv)
    locale.setlocale(locale.LC_TIME, "C")  # le date HTTP (usate da edge-tts) vanno lette in inglese
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
