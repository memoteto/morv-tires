"""Placeholder page for screens not built yet."""
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


def placeholder(phase: int) -> QWidget:
    page = QWidget()
    lay = QVBoxLayout(page)
    lay.setContentsMargins(24, 24, 24, 24)
    card = QFrame()
    card.setObjectName("card")
    inner = QVBoxLayout(card)
    inner.setContentsMargins(24, 24, 24, 24)
    note = QLabel(f"هذه الشاشة تُبنى في المرحلة {phase} من خطة V1.")
    note.setObjectName("muted")
    inner.addWidget(note)
    inner.addStretch(1)
    lay.addWidget(card)
    return page
