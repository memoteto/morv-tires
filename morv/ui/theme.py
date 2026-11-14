"""تطبيق الثيم على البرنامج كله.

القواعد التي أصلحت مشاكل البرنامج القديم (المراجعة الهندسية):
- الألوان تُبنى في QPalette أولًا، فلا يتسرب الرمادي الافتراضي لنظام التشغيل.
- ملف تنسيق QSS واحد للأشكال فقط، يُولَّد من tokens. هذا هو المكان الوحيد المسموح فيه
  باستدعاء setStyleSheet في البرنامج كله.
- اتجاه RTL يُضبط مرة واحدة على التطبيق، وكل التخطيطات تتبعه تلقائيًا.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from morv.ui.tokens import DARK, LIGHT

FONT_CANDIDATES = ["IBM Plex Sans Arabic", "Segoe UI", "Noto Sans Arabic", "Tahoma"]


def pick_font(px: int = 14) -> QFont:
    """أول خط متاح من القائمة المعتمدة، بحجم بالبكسل."""
    families = set(QFontDatabase.families())
    family = next((f for f in FONT_CANDIDATES if f in families), None)
    font = QFont(family) if family else QFont()
    font.setPixelSize(px)
    return font


def palette(t: dict) -> QPalette:
    """لوحة ألوان Qt من ألوان التصميم، للحالتين النشطة وغير النشطة، ومع حالة التعطيل."""
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "bg", QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "surface", QPalette.ColorRole.AlternateBase: "surface2",
        QPalette.ColorRole.Text: "text", QPalette.ColorRole.PlaceholderText: "muted",
        QPalette.ColorRole.Button: "surface", QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.Highlight: "select", QPalette.ColorRole.HighlightedText: "text",
        QPalette.ColorRole.ToolTipBase: "surface", QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.Mid: "border",
    }
    for group in (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive):
        for role, key in roles.items():
            p.setColor(group, role, QColor(t[key]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(t["muted"]))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(t["muted"]))
    return p


def qss(t: dict) -> str:
    """ملف التنسيق الوحيد للبرنامج. أسماء الكائنات (objectName) هي ما يربط العنصر بشكله."""
    return f"""
QWidget#side {{ background:{t['side']}; }}
QLabel#brand {{ color:#FFFFFF; font-size:17px; font-weight:700; }}
QToolButton#sideToggle {{ color:{t['side_text']}; background:transparent; border:0; border-radius:8px; padding:6px; }}
QToolButton#sideToggle:hover {{ background:{t['side_hover']}; }}
QListWidget#nav {{ background:{t['side']}; color:{t['side_text']}; border:0; outline:0; font-size:14px; }}
QListWidget#nav::item {{ height:40px; padding:0 10px; border-radius:8px; margin:2px 8px; }}
QListWidget#nav::item:hover {{ background:{t['side_hover']}; }}
QListWidget#nav::item:selected {{ background:{t['primary']}; color:{t['on_primary']}; }}
QWidget#topbar {{ background:{t['surface']}; border-bottom:1px solid {t['border']}; }}
QLabel#pageTitle {{ font-size:20px; font-weight:700; }}
QLabel#muted {{ color:{t['muted']}; }}
QFrame#card {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:12px; }}
QToolButton#topButton {{ background:transparent; border:1px solid {t['border']}; border-radius:8px; padding:6px 10px; }}
QToolButton#topButton:hover {{ background:{t['surface2']}; }}
QLineEdit {{ background:{t['surface']}; border:1px solid {t['border_strong']}; border-radius:8px;
            min-height:38px; padding:0 10px; selection-background-color:{t['select']}; }}
QLineEdit:focus {{ border:2px solid {t['primary']}; }}
QPushButton {{ background:{t['surface']}; border:1px solid {t['border_strong']}; border-radius:8px;
              min-height:38px; padding:0 14px; }}
QPushButton#primary {{ background:{t['primary']}; color:{t['on_primary']}; border:0; font-weight:600; }}
QPushButton#primary:disabled {{ background:{t['border']}; color:{t['muted']}; }}
QPushButton#segment {{ min-height:34px; padding:0 12px; border-color:{t['border']}; color:{t['muted']}; }}
QPushButton#segment:checked {{ background:{t['primary_soft']}; color:{t['primary']}; border-color:{t['primary']}; font-weight:600; }}
QPushButton#choice {{ text-align:left; padding:0 16px; }}
QPushButton#choice:checked {{ background:{t['primary_soft']}; border:2px solid {t['primary']}; color:{t['text']}; font-weight:600; }}
QTableView {{ background:{t['surface']}; alternate-background-color:{t['surface2']}; border:0;
             gridline-color:{t['border']}; selection-background-color:{t['select']}; selection-color:{t['text']}; }}
QHeaderView::section {{ background:{t['surface2']}; color:{t['muted']}; border:0;
                       border-bottom:1px solid {t['border']}; padding:0 8px; height:36px; font-weight:600; }}
QLabel#cardTitle {{ font-size:15px; font-weight:700; }}
QPushButton#danger {{ color:{t['danger']}; border-color:{t['danger']}; }}
QPushButton:disabled {{ color:{t['muted']}; border-color:{t['border']}; }}
QLabel#noticeError {{ background:{t['danger_soft']}; color:{t['danger']}; border-radius:8px; padding:8px 12px; }}
QLabel#noticeSuccess {{ background:{t['success_soft']}; color:{t['success']}; border-radius:8px; padding:8px 12px; }}
QLabel#noticeWarn {{ background:{t['warn_soft']}; color:{t['warn']}; border-radius:8px; padding:8px 12px; }}
QLabel#pillOk {{ background:{t['success_soft']}; color:{t['success']}; border-radius:14px; padding:4px 12px; max-height:28px; }}
QLabel#pillWarn {{ background:{t['warn_soft']}; color:{t['warn']}; border-radius:14px; padding:4px 12px; max-height:28px; }}
QLabel#pillDanger {{ background:{t['danger_soft']}; color:{t['danger']}; border-radius:14px; padding:4px 12px; max-height:28px; font-weight:600; }}
QLabel#keybox {{ font-family:'Courier New',monospace; font-size:22px; font-weight:700; letter-spacing:2px;
                 background:{t['surface2']}; border:2px dashed {t['border_strong']}; border-radius:12px; padding:16px; }}
QWidget#wizSide {{ background:{t['side']}; }}
QLabel#wizStep {{ color:{t['side_text']}; padding:8px 12px; border-radius:8px; }}
QLabel#wizStepCur {{ color:#FFFFFF; background:{t['side_hover']}; font-weight:600; padding:8px 12px; border-radius:8px; }}
QLabel#wizStepDone {{ color:{t['success']}; padding:8px 12px; border-radius:8px; }}
QLabel#wizBrand {{ color:#FFFFFF; font-size:18px; font-weight:700; }}
QTabWidget::pane {{ border:1px solid {t['border']}; border-radius:12px; background:{t['surface']}; top:-1px; }}
QTabBar::tab {{ padding:8px 16px; margin:0 2px; border:1px solid transparent; border-bottom:0;
                border-top-left-radius:8px; border-top-right-radius:8px; color:{t['muted']}; }}
QTabBar::tab:selected {{ background:{t['surface']}; border-color:{t['border']}; color:{t['text']}; font-weight:600; }}
QComboBox {{ background:{t['surface']}; border:1px solid {t['border_strong']}; border-radius:8px; min-height:38px; padding:0 10px; }}
QComboBox QAbstractItemView {{ background:{t['surface']}; selection-background-color:{t['select']}; border:1px solid {t['border']}; }}
QCheckBox::indicator {{ width:18px; height:18px; }}
QListWidget#userList {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:10px; }}
QListWidget#userList::item {{ height:44px; padding:0 10px; border-radius:8px; margin:2px; }}
QListWidget#userList::item:selected {{ background:{t['primary_soft']}; color:{t['text']}; }}
QToolTip {{ background:{t['surface']}; color:{t['text']}; border:1px solid {t['border_strong']};
            border-radius:6px; padding:4px 8px; }}
"""


def colors(dark: bool) -> dict:
    """ألوان الثيم المطلوب."""
    return DARK if dark else LIGHT


def apply(app, dark: bool = False) -> dict:
    """تطبيق الاتجاه والخط والألوان والتنسيق على التطبيق كله، ويعيد الألوان المستخدمة."""
    t = colors(dark)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setFont(pick_font(14))
    app.setPalette(palette(t))
    app.setStyleSheet(qss(t))
    return t
