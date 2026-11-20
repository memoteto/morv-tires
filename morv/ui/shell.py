"""Main window shell: collapsible sidebar, top bar and the page area."""
from datetime import date

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QStackedWidget, QToolButton,
                               QVBoxLayout, QWidget)

from morv import APP_NAME, VERSION
from morv.core import security
from morv.infra import backup, settings
from morv.ui import icons, theme
from morv.ui.customers_page import CustomersPage
from morv.ui.dashboard_page import DashboardPage
from morv.ui.installments_page import InstallmentsPage
from morv.ui.inventory_page import InventoryPage
from morv.ui.purchases_page import PurchasesPage
from morv.ui.pos_page import PosPage
from morv.ui.sales_page import SalesPage
from morv.ui.settings_page import SettingsPage
from morv.ui.shell_placeholder import placeholder
from morv.ui.suppliers_page import SuppliersPage
from morv.ui.reports_page import ReportsPage
from morv.ui.treasury_page import TreasuryPage
from morv.ui.widgets import ask

SIDE_WIDE = 244     # sidebar width when open
SIDE_NARROW = 72    # sidebar width when collapsed to icons

# (key, label, icon, the phase that builds the page), in sidebar order.
# A flat list, no groups or separators, by the owner's decision.
# - Sales shows the invoice and return list.
# - Purchases holds the invoice and its list; purchase returns wait for the next release.
# - Stock has tabs: items, DOT lots, stocktake.
# - Settings holds backup.
# Phase numbers as in the plan document; 0 means built.
NAV = [
    ("home", "الرئيسية", "home", 0),
    ("pos", "نقطة البيع", "pos", 4),
    ("sales", "المبيعات", "sales", 4),
    ("purchases", "المشتريات", "purchases", 3),
    ("inventory", "المخزون", "inventory", 0),
    ("customers", "العملاء", "customers", 0),
    ("suppliers", "الموردون", "suppliers", 0),
    ("installments", "التقسيط", "installments", 5),
    ("treasury", "الخزينة", "treasury", 6),
    ("reports", "التقارير", "reports", 6),
    ("settings", "الإعدادات", "settings", 0),
]


class MainWindow(QMainWindow):
    def __init__(self, session, app: QApplication):
        super().__init__()
        self.session = session
        con = self.con = session.con
        self.app = app
        self.dark = settings.get(con, "ui.theme", "light") == "dark"
        screen = QGuiApplication.primaryScreen().availableGeometry()
        # The sidebar starts collapsed on narrow screens, under 1200 logical pixels
        self.collapsed = settings.get(con, "ui.sidebar_collapsed", "1" if screen.width() < 1200 else "0") == "1"
        self.colors = self.apply_theme()
        self.setWindowTitle(APP_NAME)
        # No status bar: it spanned the window and cut the sidebar off at the bottom.
        # The version shows as a tooltip on the application name.
        # The minimum follows the screen, so the window fits small screens and high scaling
        self.setMinimumSize(min(1024, screen.width() - 40), min(640, screen.height() - 80))

        root = QWidget()
        row = QHBoxLayout(root)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self._build_side())

        main = QWidget()
        col = QVBoxLayout(main)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)
        col.addWidget(self._build_topbar())
        self.stack = QStackedWidget()
        self.pages = {}
        # Pages built on first navigation, not at startup. Only "home" is built immediately.
        self._builders = {"settings": SettingsPage, "customers": CustomersPage, "suppliers": SuppliersPage,
                          "inventory": InventoryPage, "purchases": PurchasesPage, "sales": SalesPage,
                          "pos": PosPage, "installments": InstallmentsPage,
                          "treasury": TreasuryPage, "reports": ReportsPage}
        for key, _title, _icon, phase in NAV:
            if key == "home":
                page = DashboardPage(session, on_navigate=self.dashboard_navigate)
            else:
                page = placeholder(phase)
            self.pages[key] = page
            self.stack.addWidget(page)
        col.addWidget(self.stack, 1)
        row.addWidget(main, 1)
        self.setCentralWidget(root)

        self.nav.currentRowChanged.connect(self.go)
        self.nav.setCurrentRow(0)
        self._apply_collapse()
        self._refresh_icons()
        self._shortcuts()
        self.refresh_health()
        self.health_timer = QTimer(self)
        self.health_timer.timeout.connect(self.refresh_health)
        self.health_timer.start(60_000)

    # ---------- Building ----------
    def _build_side(self) -> QWidget:
        self.side = QWidget()
        self.side.setObjectName("side")
        lay = QVBoxLayout(self.side)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.setContentsMargins(12, 0, 12, 0)
        self.side_toggle = QToolButton()
        self.side_toggle.setObjectName("sideToggle")
        self.side_toggle.setIconSize(QSize(20, 20))
        self.side_toggle.clicked.connect(self.toggle_sidebar)
        self.brand = QLabel(APP_NAME)
        self.brand.setObjectName("brand")
        self.brand.setToolTip(f"{APP_NAME} {VERSION}")
        top.addWidget(self.side_toggle)
        top.addWidget(self.brand, 1)
        lay.addLayout(top)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(20, 20))
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for key, title, _icon, _phase in NAV:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(title)
            self.nav.addItem(item)
        lay.addWidget(self.nav, 1)
        return self.side

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(60)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(10)
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.health_pill = QLabel()
        self.health_pill.setObjectName("pillOk")
        self.health_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        self.health_pill.mousePressEvent = lambda _e: self.open_backup()
        self.health_pill.setToolTip("افتح النسخ الاحتياطي")
        u = self.session.user
        self.user_label = QLabel(f"{u.display_name} · {security.ROLES[u.role]}")
        self.user_label.setObjectName("muted")
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("topButton")
        self.theme_button.setIconSize(QSize(18, 18))
        self.theme_button.clicked.connect(self.toggle_theme)
        self.logout_button = QToolButton()
        self.logout_button.setObjectName("topButton")
        self.logout_button.setIconSize(QSize(18, 18))
        self.logout_button.setToolTip("تسجيل الخروج (Ctrl+L)")
        self.logout_button.clicked.connect(self.logout)
        lay.addWidget(self.title, 1)
        lay.addWidget(self.health_pill)
        lay.addWidget(self.user_label)
        lay.addWidget(self.theme_button)
        lay.addWidget(self.logout_button)
        return bar

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+B"), self, activated=self.toggle_sidebar)
        QShortcut(QKeySequence("Ctrl+D"), self, activated=self.toggle_theme)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.logout)
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.close)

    def refresh_health(self) -> None:
        level, text = backup.health(self.con)
        self.health_pill.setText({"ok": "النسخ الاحتياطي سليم", "warn": "تنبيه النسخ الاحتياطي",
                                  "danger": "خلل في النسخ الاحتياطي"}[level])
        self.health_pill.setObjectName({"ok": "pillOk", "warn": "pillWarn", "danger": "pillDanger"}[level])
        self.health_pill.setToolTip(text)
        self.health_pill.style().unpolish(self.health_pill)
        self.health_pill.style().polish(self.health_pill)

    def open_contract(self, sale_id: int) -> None:
        self.go_key("installments")
        self.pages["installments"].open_form_for_sale(sale_id)

    def open_backup(self) -> None:
        self.go_key("settings")
        page = self.pages["settings"]
        page.tabs.setCurrentWidget(page.backup_tab)
        page.backup_tab.refresh()

    def logout(self) -> None:
        self.session.on_logout()

    def closeEvent(self, event) -> None:
        if backup.is_encryption_ready(self.con):
            backup.run_scheduled(self.con, self.session.user.username)
            last_usb = settings.get(self.con, "backup.last_usb_at", "")[:10]
            today = date.today().isoformat()
            if backup.usb_available(self.con) and last_usb != today and ask(
                    self, "نسخة على الفلاشة", "الفلاشة موصولة ولم تُنسخ إليها نسخة اليوم. ننسخ الآن قبل الإغلاق؟"):
                try:
                    backup.copy_to_usb(self.con, self.session.user.username)
                except backup.BackupError:
                    pass
        super().closeEvent(event)

    # ---------- Behaviour ----------
    def go(self, row: int) -> None:
        if row < 0:
            return
        key = NAV[row][0]
        first_visit = key in self._builders
        # Build the page on first visit if it was deferred.
        if first_visit:
            page = self._builders.pop(key)(self.session)
            self.pages[key] = page
            self.stack.insertWidget(row, page)
            old = self.stack.widget(row + 1)   # the placeholder is now one slot higher
            self.stack.removeWidget(old)
            old.deleteLater()
            # Wire up signals that require the real page.
            if key == "pos":
                page.contract_needed.connect(self.open_contract)
        self.stack.setCurrentIndex(row)
        self.title.setText(NAV[row][1])
        # Other screens may have changed the data since this one was last shown; home refreshes itself.
        if not first_visit and hasattr(self.pages[key], "refresh"):
            self.pages[key].refresh()
        if key == "settings" and hasattr(self, "health_pill"):
            self.refresh_health()

    def go_key(self, key: str) -> None:
        for i, entry in enumerate(NAV):
            if entry[0] == key:
                self.nav.setCurrentRow(i)
                return
        raise KeyError(key)

    def dashboard_navigate(self, key: str) -> None:
        self.go_key(key)
        if key == "purchases":
            self.pages["purchases"].open_form()

    def apply_theme(self) -> dict:
        family = settings.get(self.con, "ui.font_family", theme.DEFAULT_FONT)
        size = int(settings.get(self.con, "ui.font_size", str(theme.DEFAULT_SIZE)))
        return theme.apply(self.app, self.dark, family, size)

    def set_font(self, family: str, size: int) -> None:
        settings.put(self.con, "ui.font_family", family)
        settings.put(self.con, "ui.font_size", str(size))
        self.colors = self.apply_theme()
        self._refresh_icons()

    def toggle_theme(self) -> None:
        self.dark = not self.dark
        self.colors = self.apply_theme()
        settings.put(self.con, "ui.theme", "dark" if self.dark else "light")
        self._refresh_icons()

    def toggle_sidebar(self) -> None:
        self.collapsed = not self.collapsed
        settings.put(self.con, "ui.sidebar_collapsed", "1" if self.collapsed else "0")
        self._apply_collapse()
        self._refresh_icons()

    def _apply_collapse(self) -> None:
        self.side.setFixedWidth(SIDE_NARROW if self.collapsed else SIDE_WIDE)
        self.brand.setVisible(not self.collapsed)
        for i, entry in enumerate(NAV):
            self.nav.item(i).setText("" if self.collapsed else entry[1])

    def _refresh_icons(self) -> None:
        c = self.colors
        for i, entry in enumerate(NAV):
            self.nav.item(i).setIcon(icons.icon(entry[2], c["side_text"]))
        # The arrow points where it will move: open to the left, collapse to the right
        self.side_toggle.setIcon(icons.icon("expand" if self.collapsed else "collapse", c["side_text"]))
        self.side_toggle.setToolTip("فتح الشريط" if self.collapsed else "طي الشريط")
        self.theme_button.setIcon(icons.icon("sun" if self.dark else "moon", c["text"], 18))
        self.theme_button.setToolTip("الوضع الفاتح (Ctrl+D)" if self.dark else "الوضع الداكن (Ctrl+D)")
        self.logout_button.setIcon(icons.icon("logout", c["text"], 18))
