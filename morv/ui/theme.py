"""Apply the theme to the whole application."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from morv.ui.tokens import DARK, LIGHT

# OFL Arabic fonts shipped with the app, so it never depends on what Windows has
FONTS_DIR = Path(__file__).with_name("fonts")
BUNDLED = {"IBM Plex Sans Arabic": ["IBMPlexSansArabic-Regular.ttf", "IBMPlexSansArabic-Bold.ttf"],
           "Cairo": ["Cairo-Variable.ttf"]}
FONT_CHOICES = ["IBM Plex Sans Arabic", "Cairo", "Segoe UI", "Tahoma"]
DEFAULT_FONT = "IBM Plex Sans Arabic"
FONT_SIZES = (13, 14, 15, 16, 17, 18)
DEFAULT_SIZE = 14
_loaded = False


def load_bundled_fonts() -> None:
    global _loaded
    if _loaded:
        return
    for files in BUNDLED.values():
        for name in files:
            f = FONTS_DIR / name
            if f.exists():
                QFontDatabase.addApplicationFont(str(f))
    _loaded = True


def available_fonts() -> list[str]:
    load_bundled_fonts()
    families = set(QFontDatabase.families())
    return [f for f in FONT_CHOICES if f in families or f in BUNDLED]


def pick_font(px: int = DEFAULT_SIZE, family: str = DEFAULT_FONT) -> QFont:
    load_bundled_fonts()
    families = set(QFontDatabase.families())
    if family not in families:
        family = next((f for f in FONT_CHOICES if f in families), "")
    font = QFont(family) if family else QFont()
    font.setPixelSize(int(px))
    return font


def palette(t: dict) -> QPalette:
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
    from morv.ui import icons
    # QSS takes arrow images only as files, so the theme colour is drawn into one per mode
    t = {"arrow": icons.svg_file("chevron_down", t["muted"]),
         "arrow_off": icons.svg_file("chevron_down", t["border_strong"]), **t}
    return f"""
QWidget#side {{ background:{t['side']}; }}
QLabel#brand {{ color:#FFFFFF; font-size:{t['fs']+3}px; font-weight:700; }}
QToolButton#sideToggle {{ color:{t['side_text']}; background:transparent; border:0; border-radius:8px; padding:6px; }}
QToolButton#sideToggle:hover {{ background:{t['side_hover']}; }}
QListWidget#nav {{ background:{t['side']}; color:{t['side_text']}; border:0; outline:0; font-size:{t['fs']}px; }}
QListWidget#nav::item {{ height:40px; padding:0 10px; border-radius:8px; margin:2px 8px; }}
QListWidget#nav::item:hover {{ background:{t['side_hover']}; }}
QListWidget#nav::item:selected {{ background:{t['primary']}; color:{t['on_primary']}; }}
QWidget#topbar {{ background:{t['surface']}; border-bottom:1px solid {t['border']}; }}
QLabel#pageTitle {{ font-size:{t['fs']+6}px; font-weight:700; }}
QLabel#muted {{ color:{t['muted']}; }}
QLabel#loginLogo {{ background:{t['primary']}; color:{t['on_primary']}; border-radius:14px;
                   font-size:24px; font-weight:700; }}
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
QPushButton#kpiTile {{ text-align:right; min-height:70px; border-radius:14px; padding:10px 14px; }}
QPushButton#kpiTile:hover {{ border-color:{t['primary']}; }}
QPushButton#kpiTile:checked {{ border:2px solid {t['primary']}; background:{t['primary_soft']}; }}
QLabel#cardTitleDanger {{ font-size:{t['fs']+1}px; font-weight:700; color:{t['danger']}; }}
QFrame#barFill {{ background:{t['primary']}; border-radius:4px; }}
QPushButton#qaTile {{ text-align:right; min-height:58px; border-radius:14px; padding:10px 14px; }}
QPushButton#qaTile:hover {{ border-color:{t['primary']}; }}
QPushButton#qaMain {{ text-align:right; min-height:58px; border-radius:14px; padding:10px 14px;
                     background:{t['primary']}; color:{t['on_primary']}; border:0; }}
QLabel#qaTitle {{ font-weight:600; }}
QLabel#qaSub {{ color:{t['muted']}; font-size:{t['fs']-1}px; }}
QPushButton#qaMain QLabel#qaTitle, QPushButton#qaMain QLabel#qaSub {{ color:{t['on_primary']}; }}
QLabel#qaIcon {{ background:{t['primary_soft']}; border-radius:10px; }}
QPushButton#qaMain QLabel#qaIcon {{ background:rgba(255,255,255,0.18); }}
QTableView {{ background:{t['surface']}; alternate-background-color:{t['surface2']}; border:0;
             gridline-color:{t['border']}; selection-background-color:{t['select']}; selection-color:{t['text']}; }}
QTableView::item {{ padding:0 10px; }}
QHeaderView {{ background:{t['surface2']}; }}
QHeaderView::section {{ background:{t['surface2']}; color:{t['muted']}; border:0;
                       border-bottom:1px solid {t['border']}; padding:0 8px; height:36px; font-weight:600; }}
QTableCornerButton::section {{ background:{t['surface2']}; border:0;
                              border-bottom:1px solid {t['border']}; }}
QLabel#cardTitle {{ font-size:{t['fs']+1}px; font-weight:700; }}
QFrame#summarySection {{ border:0; border-bottom:1px solid {t['border']}; }}
QLabel#summarySectionTitle {{ font-size:{t['fs']+1}px; font-weight:700; }}
QFrame#summaryMetric, QFrame#summaryDetails {{ background:{t['surface2']};
                                                  border:1px solid {t['border']}; border-radius:10px; }}
QLabel#summaryMetricValue {{ font-size:{t['fs']+3}px; font-weight:700; }}
QFrame#summaryNote {{ background:{t['surface2']}; border:1px solid {t['border']};
                      border-radius:10px; }}
QTableView#summaryTable {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:8px; }}
QWidget#summaryAdaptiveTable {{ background:transparent; }}
QScrollArea#summaryCardsScroll {{ background:transparent; border:0; }}
QScrollBar:vertical {{
    width:8px; background:{t['surface2']}; border:0; margin:0; border-radius:4px;
}}
QScrollBar::handle:vertical {{
    background:{t['border_strong']}; min-height:28px; border-radius:4px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height:0; border:0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background:transparent; }}
QScrollBar:horizontal {{
    height:10px; background:{t['surface2']}; border:0; margin:0; border-radius:4px;
}}
QScrollBar::handle:horizontal {{
    background:{t['border_strong']}; min-width:28px; border-radius:4px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width:0; border:0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background:transparent; }}
QWidget#summaryDetailRow {{ background:transparent; }}
QFrame#summaryRecord {{ background:{t['surface2']}; border:1px solid {t['border']};
                        border-radius:10px; }}
QLabel#summaryRecordLabel {{ color:{t['muted']}; }}
QLabel#summaryRecordTitle {{ font-size:{t['fs']+1}px; font-weight:700; }}
QLabel#summaryRecordValue {{ font-size:{t['fs']+1}px; font-weight:600; }}
QPushButton#danger {{ color:{t['danger']}; border-color:{t['danger']}; }}
QPushButton:disabled {{ color:{t['muted']}; border-color:{t['border']}; }}
QLabel#noticeError {{ background:{t['danger_soft']}; color:{t['danger']}; border-radius:8px; padding:8px 12px; }}
QLabel#noticeSuccess {{ background:{t['success_soft']}; color:{t['success']}; border-radius:8px; padding:8px 12px; }}
QLabel#noticeWarn {{ background:{t['warn_soft']}; color:{t['warn']}; border-radius:8px; padding:8px 12px; }}
QLabel#pillOk {{ background:{t['success_soft']}; color:{t['success']}; border-radius:14px; padding:4px 12px; max-height:28px; }}
QLabel#pillWarn {{ background:{t['warn_soft']}; color:{t['warn']}; border-radius:14px; padding:4px 12px; max-height:28px; }}
QLabel#pillDanger {{ background:{t['danger_soft']}; color:{t['danger']}; border-radius:14px; padding:4px 12px; max-height:28px; font-weight:600; }}
QLabel#costUp {{ color:{t['danger']}; font-size:{t['fs']-2}px; }}
QLabel#costDown {{ color:{t['success']}; font-size:{t['fs']-2}px; }}
QPushButton#stepperBtn {{ min-width:28px; max-width:28px; min-height:28px; max-height:28px;
                         padding:0; font-weight:700; }}
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
QComboBox {{ background:{t['surface']}; border:1px solid {t['border_strong']}; border-radius:8px; min-height:38px;
             padding-left:32px; padding-right:10px; }}
QComboBox::drop-down {{ subcontrol-origin:border; subcontrol-position:top left; width:30px; border:0;
                       background:transparent; }}
QComboBox::down-arrow {{ image:url({t.get('arrow', '')}); width:12px; height:12px; }}
QComboBox::down-arrow:disabled {{ image:url({t.get('arrow_off', '')}); }}
QComboBox QAbstractItemView {{ background:{t['surface']}; selection-background-color:{t['select']}; border:1px solid {t['border']}; }}
QComboBox#statementFilter {{ padding-left:30px; padding-right:10px; }}
QComboBox#statementFilter::drop-down {{ subcontrol-origin:border; subcontrol-position:top left;
                                       width:26px; border:0; background:transparent; }}
QComboBox#statementFilter::down-arrow {{ image:none; width:10px; height:10px; }}
QCheckBox::indicator {{ width:18px; height:18px; }}
QListWidget#userList {{ background:{t['surface']}; border:1px solid {t['border']}; border-radius:10px; }}
QListWidget#userList::item {{ padding:10px; border-radius:8px; }}
QListWidget#userList::item:selected {{ background:{t['primary_soft']}; color:{t['text']}; }}
QDateEdit#statementDate {{ background:{t['surface']}; color:{t['text']};
                              border:1px solid {t['border_strong']}; border-radius:8px;
                              min-height:38px; padding:0 8px 0 28px; }}
QDateEdit#statementDate:focus {{ border:2px solid {t['primary']}; }}
QDateEdit#statementDate::drop-down {{ subcontrol-origin:border; subcontrol-position:top left;
                                     width:26px; border:0; background:transparent; }}
QDateEdit#statementDate::down-arrow {{ image:none; width:10px; height:10px; }}
QCalendarWidget#statementCalendar {{ background:{t['surface']}; }}
QCalendarWidget#statementCalendar QWidget#qt_calendar_navigationbar {{ background:{t['surface2']}; }}
QCalendarWidget#statementCalendar QToolButton {{ color:{t['text']}; background:{t['surface2']};
                                                   border:0; min-height:0; max-height:30px;
                                                   padding:0 4px; }}
QCalendarWidget#statementCalendar QAbstractItemView {{ background:{t['surface']}; color:{t['text']};
                                                        selection-background-color:{t['primary']};
                                                        selection-color:{t['on_primary']}; }}
QLabel#statementCustomerName {{ font-size:{t['fs']+2}px; font-weight:700; }}
QFrame#statementKpiCard {{ background:{t['surface2']}; border:1px solid {t['border']};
                           border-radius:10px; }}
QLabel#statementKpiValue {{ font-size:{t['fs']+3}px; font-weight:700; }}
QLabel#statementSectionTitle, QLabel#statementEmptyTitle {{ font-weight:700; }}
QFrame#modalFrame {{ background:{t['surface']}; border:1px solid {t['border_strong']}; border-radius:14px; }}
QWidget#modalHeader {{ background:{t['surface2']}; border-top-left-radius:14px; border-top-right-radius:14px; border-bottom:1px solid {t['border']}; }}
QLabel#modalTitle {{ font-size:{t['fs']+2}px; font-weight:700; }}
QWidget#modalBody {{ background:{t['surface']}; }}
QWidget#modalFooter {{ background:{t['surface2']}; border-top:1px solid {t['border']}; border-bottom-left-radius:14px; border-bottom-right-radius:14px; }}
QScrollArea {{ background:transparent; border:0; }}
QScrollArea#previewScroll {{ background:transparent; border:0; }}
QScrollArea > QWidget > QWidget {{ background:transparent; }}
QSpinBox {{ background:{t['surface']}; color:{t['text']}; border:1px solid {t['border_strong']}; border-radius:8px; min-height:38px; padding:0 10px; }}
QPlainTextEdit {{ background:{t['surface']}; color:{t['text']}; border:1px solid {t['border_strong']}; border-radius:8px; padding:6px 10px; }}
QToolTip {{ background:{t['surface']}; color:{t['text']}; border:1px solid {t['border_strong']};
            border-radius:6px; padding:4px 8px; }}
QWidget#cardHead {{ background:transparent; border:0; border-bottom:1px solid {t['border']}; }}
QLabel#fieldLabel {{ color:{t['muted']}; font-size:{t['fs']-1}px; }}
QLineEdit:read-only {{ background:{t['surface2']}; border-style:dashed; border-color:{t['border_strong']}; }}
QFrame#infoStrip {{ background:{t['surface2']}; border:1px solid {t['border']}; border-radius:10px; }}
QLabel#chip {{ background:{t['surface2']}; color:{t['muted']}; border:1px solid {t['border']}; border-radius:6px;
               padding:2px 8px; font-size:{t['fs']-2}px; }}
QLabel#kbd {{ background:{t['surface2']}; color:{t['muted']}; border:1px solid {t['border']}; border-radius:5px;
              padding:1px 6px; font-size:{t['fs']-3}px; }}
QLabel#kbdOnPrimary {{ background:rgba(255,255,255,0.18); color:{t['on_primary']}; border:1px solid rgba(255,255,255,0.45);
                       border-radius:5px; padding:1px 6px; font-size:{t['fs']-3}px; }}
QPushButton#link {{ background:transparent; border:0; color:{t['primary']}; min-height:28px; padding:0 6px; font-weight:600; }}
QPushButton#dangerLink {{ background:transparent; border:0; color:{t['danger']}; padding:0 8px; }}
QFrame#segBar {{ background:{t['surface2']}; border:1px solid {t['border']}; border-radius:10px; }}
QPushButton#tab {{ background:transparent; border:0; border-radius:8px; min-height:34px; padding:0 14px; color:{t['muted']}; }}
QPushButton#tab:checked {{ background:{t['surface']}; color:{t['text']}; border:1px solid {t['border']}; font-weight:600; }}
QPushButton#quickChip {{ min-height:30px; padding:0 10px; border-color:{t['border']}; font-size:{t['fs']-1}px; }}
QToolButton#stepBtn {{ background:{t['surface']}; border:1px solid {t['border_strong']}; border-radius:8px;
                       min-width:34px; min-height:36px; }}
QFrame#totalBox {{ background:{t['primary_soft']}; border:0; border-radius:10px; }}
QLabel#totalLabel {{ color:{t['primary']}; font-size:{t['fs']+2}px; font-weight:700; }}
QLabel#totalValue {{ color:{t['primary']}; font-size:{t['fs']+3}px; font-weight:700; }}
QFrame#payBox {{ background:{t['surface2']}; border:1px solid {t['border']}; border-radius:10px; }}
QFrame#changeBox {{ background:{t['success_soft']}; border:0; border-radius:8px; }}
QLabel#changeLabel {{ color:{t['success']}; font-size:{t['fs']+1}px; font-weight:700; }}
QLabel#changeValue {{ color:{t['success']}; font-size:{t['fs']+4}px; font-weight:700; }}
QComboBox#payMethod {{ min-height:56px; padding-left:32px; padding-right:12px; font-weight:600; }}
QPushButton#primaryLarge {{ background:{t['primary']}; color:{t['on_primary']}; border:0; border-radius:10px;
                           min-height:54px; font-size:{t['fs']+4}px; font-weight:700; }}
QPushButton#primaryLarge:disabled {{ background:{t['border']}; color:{t['muted']}; }}
"""


def colors(dark: bool) -> dict:
    return DARK if dark else LIGHT


def apply(app, dark: bool = False, font_family: str = DEFAULT_FONT, font_size: int = DEFAULT_SIZE) -> dict:
    t = dict(colors(dark))
    t["fs"] = int(font_size)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setFont(pick_font(font_size, font_family))
    app.setPalette(palette(t))
    app.setStyleSheet(qss(t))
    return t
