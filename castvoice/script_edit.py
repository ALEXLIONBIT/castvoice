"""Casella di testo a più voci: ogni pezzo di testo ha la sua voce, riconoscibile dal colore.

Si sceglie una voce (pulsante o Alt+numero) e si continua a scrivere: da lì in poi il testo è
di quella voce. Con del testo selezionato, la scelta della voce cambia solo la selezione.
Il testo si legge come lista di pezzi [(id_voce, testo), …] da far dire uno dopo l'altro.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextFormat
from PySide6.QtWidgets import QTextEdit

VOICE_PROP = int(QTextFormat.UserProperty) + 1  # proprietà del carattere che contiene l'id della voce
PALETTE = ["#22d3ee", "#f59e0b", "#a78bfa", "#34d399", "#f472b6", "#60a5fa", "#fb7185", "#a3e635", "#fbbf24"]


def voice_color(voice_id: str, order: list[str]) -> str:
    """Colore stabile di una voce: segue la posizione tra le scorciatoie, altrimenti l'id."""
    if voice_id in order:
        return PALETTE[order.index(voice_id) % len(PALETTE)]
    return PALETTE[sum(map(ord, voice_id)) % len(PALETTE)]


class ScriptEdit(QTextEdit):
    voice_changed = Signal(str)  # il cursore è entrato nel testo di un'altra voce

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptRichText(False)  # i testi incollati prendono la voce corrente, senza formattazione
        self.order: list[str] = []     # ordine delle scorciatoie, per i colori
        self._voice = ""
        self.cursorPositionChanged.connect(self._follow_cursor)

    # ---- voce corrente -----------------------------------------------------
    def _fmt(self, voice_id: str) -> QTextCharFormat:
        f = QTextCharFormat()
        f.setProperty(VOICE_PROP, voice_id)
        f.setForeground(QColor(voice_color(voice_id, self.order)))
        return f

    def current_voice(self) -> str:
        return self._voice

    def set_voice(self, voice_id: str, to_selection: bool = True) -> None:
        """Da qui in poi si scrive con questa voce; con del testo selezionato cambia anche la selezione."""
        self._voice = voice_id
        cur = self.textCursor()
        if to_selection and cur.hasSelection():
            cur.mergeCharFormat(self._fmt(voice_id))
        self.setCurrentCharFormat(self._fmt(voice_id))

    def _follow_cursor(self) -> None:
        v = self.currentCharFormat().property(VOICE_PROP)
        if v and v != self._voice:
            self._voice = v
            self.voice_changed.emit(v)

    def recolor(self) -> None:
        """Riapplica i colori (dopo che è cambiato l'ordine delle scorciatoie)."""
        voice = self._voice
        segs = self.segments(voice)
        self.set_segments(segs)
        self.set_voice_silently(voice)

    def set_voice_silently(self, voice_id: str) -> None:
        self._voice = voice_id
        self.setCurrentCharFormat(self._fmt(voice_id))

    # ---- lettura e scrittura dei pezzi ------------------------------------------
    def segments(self, default_voice: str) -> list[tuple[str, str]]:
        """[(id_voce, testo)] con i pezzi vicini della stessa voce uniti e quelli vuoti scartati."""
        out: list[list[str]] = []

        def add(voice: str, text: str) -> None:
            if out and out[-1][0] == voice:
                out[-1][1] += text
            else:
                out.append([voice, text])

        block = self.document().begin()
        first = True
        while block.isValid():
            if not first:
                add(out[-1][0] if out else default_voice, "\n")
            first = False
            text = block.text()
            covered = 0
            for r in sorted(block.textFormats(), key=lambda r: r.start):
                if r.start > covered:  # testo senza formato: voce predefinita
                    add(default_voice, text[covered:r.start])
                voice = r.format.property(VOICE_PROP) or default_voice
                add(voice, text[r.start:r.start + r.length])
                covered = r.start + r.length
            if covered < len(text):
                add(default_voice, text[covered:])
            block = block.next()
        return [(v, t.strip()) for v, t in out if t.strip()]

    def set_segments(self, segs: list[tuple[str, str]]) -> None:
        self.clear()
        cur = self.textCursor()
        for voice, text in segs:
            cur.insertText(text + " ", self._fmt(voice))
        if segs:
            self.set_voice_silently(segs[-1][0])
