"""الصفحة المؤقتة للشاشات التي لم تُبنَ بعد (في ملف مستقل حتى تستخدمها الصفحات دون استيراد النافذة)."""
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget


def placeholder(phase: int) -> QWidget:
    """صفحة مؤقتة تخبر المستخدم أن الشاشة لم تُبنَ بعد وفي أي مرحلة ستُبنى.

    لا تكرر عنوان الصفحة لأنه ظاهر في الشريط العلوي.
    """
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
