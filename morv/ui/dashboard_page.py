"""Home dashboard: quick actions, period KPIs, a sales chart, alerts, recent invoices and top sellers."""
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import dashboard_view
from morv.ui import icons, tokens
from morv.ui.pos_page import PAYMENT_LABELS
from morv.ui.widgets import (card, ICON_PRIMARY, kpi_tile, muted, Notice)
from morv.ui.workers import run_async

PERIODS = dashboard_view.PERIODS
ALERT_LEVEL = {"bad": "error", "warn": "warn", "ok": "success"}
BALANCE_LABELS = (("1110", "الصندوق"), ("1130", "الخزنة"), ("1210", "بنك التحويلات"),
                  ("1220", "البطاقات"), ("1230", "المحافظ الإلكترونية"))
# Collect.dc.html/Purchase.dc.html/Statement.dc.html have no standalone search-first entry point yet,
# so these route to the nearest existing screen instead of a deep link; see design_decisions.md #15.
# The icon name is one of morv.ui.icons.PATHS, matching the sidebar icon for that same screen.
QUICK_ACTIONS = (("pos", "pos", "فاتورة بيع جديدة", "نقطة البيع", True),
                 ("installments", "installments", "تحصيل قسط", "بالاسم أو الموبايل", False),
                 ("purchases", "purchases", "استلام بضاعة", "فاتورة شراء", False),
                 ("customers", "customers", "كشف حساب عميل", "الحركات والرصيد", False))


class DashboardPage(QWidget):
    def __init__(self, session, on_navigate: Callable[[str], None] | None = None, parent=None):
        super().__init__(parent)
        self.session = session
        self.on_navigate = on_navigate
        self.period = "today"
        self.kpi_values: dict[str, tuple] = {}
        self.period_buttons: dict[str, QPushButton] = {}
        self._fetch_gen = 0
        self._show_timer = QTimer(self)
        self._show_timer.setSingleShot(True)
        self._show_timer.timeout.connect(self.reload)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        root = QVBoxLayout(inner)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(14)
        root.addWidget(self._header())
        root.addLayout(self._quick_actions())
        root.addLayout(self._kpis())
        root.addWidget(self._balances_card())

        row1 = QHBoxLayout()
        row1.setSpacing(14)
        # AlignTop keeps the chart card at its own natural height instead of stretching
        # to match the (often taller) alerts card beside it.
        row1.addWidget(self._chart_card(), 2, Qt.AlignmentFlag.AlignTop)
        row1.addWidget(self._alerts_card(), 1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(14)
        row2.addWidget(self._recent_card(), 1)
        row2.addWidget(self._breakdown_card(), 1)
        root.addLayout(row2)
        root.addStretch(1)
        # Data loaded in showEvent only; no eager reload here to avoid blocking startup.

    def _header(self) -> QFrame:
        w = QFrame()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        title = QLabel("لوحة التحكم")
        title.setObjectName("pageTitle")
        h.addWidget(title)
        h.addStretch(1)
        for key, label in PERIODS:
            b = QPushButton(label)
            b.setObjectName("tab")
            b.setCheckable(True)
            b.setChecked(key == self.period)
            b.clicked.connect(lambda _c=False, k=key: self._pick_period(k))
            self.period_buttons[key] = b
            h.addWidget(b)
        return w

    def _pick_period(self, key: str) -> None:
        self.period = key
        for k, b in self.period_buttons.items():
            b.setChecked(k == key)
        self.reload()

    def _quick_actions(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(10)
        for i, (key, icon_name, title, sub, main) in enumerate(QUICK_ACTIONS):
            btn = QPushButton()
            btn.setObjectName("qaMain" if main else "qaTile")
            outer = QHBoxLayout(btn)
            outer.setContentsMargins(14, 10, 14, 10)
            outer.setSpacing(12)
            ic = QLabel()
            ic.setObjectName("qaIcon")
            ic.setFixedSize(38, 38)
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ic.setPixmap(icons.icon(icon_name, tokens.ICON_ON_PRIMARY if main else ICON_PRIMARY, 20).pixmap(20, 20))
            outer.addWidget(ic)
            col = QVBoxLayout()
            col.setSpacing(2)
            title_lbl = QLabel(title)
            title_lbl.setObjectName("qaTitle")
            col.addWidget(title_lbl)
            sub_lbl = QLabel(sub)
            sub_lbl.setObjectName("qaSub")
            col.addWidget(sub_lbl)
            outer.addLayout(col, 1)
            if self.on_navigate:
                btn.clicked.connect(lambda _c=False, k=key: self.on_navigate(k))
            grid.addWidget(btn, 0, i)
        return grid

    def _kpis(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(10)
        specs = (("sales", "المبيعات", False), ("coll", "المحصَّل نقدًا وإلكترونيًا", False),
                 ("due", "أقساط مستحقة ومتأخرة", True), ("cash", "إجمالي الأموال الآن", False))
        for i, (key, title, danger) in enumerate(specs):
            box, value, sub = kpi_tile(title, danger=danger)
            self.kpi_values[key] = (value, sub)
            grid.addWidget(box, 0, i)
        return grid

    def _balances_card(self) -> QFrame:
        box, lay = card("أرصدة الصندوق والحسابات الإلكترونية الآن")
        row = QHBoxLayout()
        row.setSpacing(14)
        self.balance_values: dict[str, QLabel] = {}
        for code, title in BALANCE_LABELS:
            col = QVBoxLayout()
            col.addWidget(muted(title))
            value = QLabel("—")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignAbsolute)
            col.addWidget(value)
            self.balance_values[code] = value
            row.addLayout(col, 1)
        lay.addLayout(row)
        return box

    def _chart_card(self) -> QFrame:
        box, lay = card("مبيعات آخر 7 أيام")
        # Fixed cap matching the approved design's plot height, so this card never stretches
        # to match a taller sibling (the alerts card) regardless of layout-stretch quirks.
        box.setMaximumHeight(230)
        self.chart_row = QHBoxLayout()
        self.chart_row.setSpacing(8)
        lay.addLayout(self.chart_row, 1)
        return box

    def _alerts_card(self) -> QFrame:
        box, lay = card("تحتاج إجراء")
        self.alerts_col = QVBoxLayout()
        self.alerts_col.setSpacing(8)
        lay.addLayout(self.alerts_col)
        return box

    def _recent_card(self) -> QFrame:
        box, lay = card("آخر فواتير البيع")
        self.recent_col = QVBoxLayout()
        self.recent_col.setSpacing(6)
        lay.addLayout(self.recent_col)
        return box

    def _breakdown_card(self) -> QFrame:
        box, lay = card("طرق الدفع اليوم")
        self.pay_col = QVBoxLayout()
        self.pay_col.setSpacing(6)
        lay.addLayout(self.pay_col)
        lay.addWidget(muted("الأصناف الأكثر مبيعًا هذا الشهر (قطعة)"))
        self.top_col = QVBoxLayout()
        self.top_col.setSpacing(6)
        lay.addLayout(self.top_col)
        return box

    def reload(self) -> None:
        self._fetch_gen += 1
        gen = self._fetch_gen
        period = self.period

        def on_done(view, _err):
            if gen == self._fetch_gen:
                self._on_data(view)

        run_async(lambda con: dashboard_view.get(con, period=period), on_done, self, self.session.con)

    def _on_data(self, v) -> None:
        if v is None:
            return
        period_label = dict(PERIODS)[self.period]
        self.kpi_values["sales"][0].setText(ltr(fmt(v.sales_total)))
        self.kpi_values["sales"][1].setText(f"{v.sales_count} فاتورة · {period_label}")
        self.kpi_values["coll"][0].setText(ltr(fmt(v.collected_total)))
        self.kpi_values["coll"][1].setText(period_label)
        self.kpi_values["due"][0].setText(ltr(fmt(v.due_amount)))
        self.kpi_values["due"][1].setText(f"{v.due_count} عقدًا")
        self.kpi_values["cash"][0].setText(ltr(fmt(v.cash_balance)))
        self.kpi_values["cash"][1].setText(self._closing_text(v))
        for code, amount in v.account_balances:
            self.balance_values[code].setText(ltr(fmt(amount)))
        self._fill_chart(v.chart_days)
        self._fill_alerts(v.alerts)
        self._fill_recent(v.recent)
        self._fill_breakdown(v.pay_today, v.top_month)

    @staticmethod
    def _closing_text(v) -> str:
        if v.last_closing is None:
            text = "لم يُقفل الصندوق بعد"
        else:
            text = f"آخر إقفال {ltr(v.last_closing[0])} · {ltr(v.last_closing[1][11:16])}"
        return text + (f" · {v.unclosed_days} يوم بلا إقفال" if v.unclosed_days else "")

    def _fill_chart(self, days: tuple) -> None:
        self._clear(self.chart_row)
        top = max((t for _, t in days), default=0) or 1
        for day, total in days:
            col = QVBoxLayout()
            col.setSpacing(4)
            col.setAlignment(Qt.AlignmentFlag.AlignBottom)
            val = muted(ltr(fmt(total)) if total else "")
            val.setAlignment(Qt.AlignmentFlag.AlignCenter)
            bar = QFrame()
            bar.setObjectName("barFill")
            bar.setFixedWidth(28)
            bar.setFixedHeight(max(4, int(total / top * 90)))
            lbl = muted(day[5:])
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            col.addWidget(val)
            col.addWidget(bar, 0, Qt.AlignmentFlag.AlignHCenter)
            col.addWidget(lbl)
            w = QWidget()
            w.setLayout(col)
            self.chart_row.addWidget(w, 1)

    def _fill_alerts(self, alerts: tuple) -> None:
        self._clear(self.alerts_col)
        if not alerts:
            self.alerts_col.addWidget(muted("لا توجد تنبيهات الآن."))
            return
        for a in alerts:
            lbl = Notice()
            lbl.show_message(self._alert_text(a), ALERT_LEVEL[a.tone])
            self.alerts_col.addWidget(lbl)

    @staticmethod
    def _alert_text(a) -> str:
        if a.kind == "late":
            return f"{a.count} عقود متأخرة · {ltr(fmt(a.amount))} — {a.detail}"
        if a.kind == "due_today":
            head = "قسط مستحق اليوم" if a.count == 1 else f"{a.count} أقساط مستحقة اليوم"
            tail = f" · {a.detail}" if a.detail else ""
            return f"{head} · {ltr(fmt(a.amount))}{tail}"
        if a.kind == "old_dot":
            head = "إطار عمره 3 سنوات فأكثر" if a.count == 1 else f"{a.count} إطارات عمرها 3 سنوات فأكثر"
            return f"{head} — {a.detail}" if a.detail else head
        if a.kind == "safe_count":
            when = "لم تُجرد الخزنة بعد" if a.count < 0 else f"مضت {a.count} أيام على آخر جرد للخزنة"
            return f"{when} · رصيدها {ltr(fmt(a.amount))}"
        return a.detail

    def _fill_recent(self, recent: tuple) -> None:
        self._clear(self.recent_col)
        if not recent:
            self.recent_col.addWidget(muted("لا توجد فواتير بعد."))
            return
        for number, cust, methods, total, sale_date in recent:
            row = QHBoxLayout()
            row.addWidget(QLabel(ltr(number)))
            row.addWidget(QLabel(cust), 1)
            row.addWidget(muted(PAYMENT_LABELS.get(methods, methods or "")))
            row.addWidget(QLabel(ltr(fmt(total))))
            row.addWidget(muted(ltr(sale_date)))
            w = QWidget()
            w.setLayout(row)
            self.recent_col.addWidget(w)

    def _fill_breakdown(self, pay_today: tuple, top_month: tuple) -> None:
        self._clear(self.pay_col)
        pay_top = max((amt for _, amt in pay_today), default=0) or 1
        for method, amount in pay_today:
            row = QHBoxLayout()
            row.addWidget(QLabel(PAYMENT_LABELS.get(method, method)), 1)
            bar = QFrame()
            bar.setObjectName("barFill")
            bar.setFixedHeight(10)
            bar.setFixedWidth(max(4, int(amount / pay_top * 140)))
            row.addWidget(bar)
            row.addWidget(muted(ltr(fmt(amount))))
            w = QWidget()
            w.setLayout(row)
            self.pay_col.addWidget(w)
        if not pay_today:
            self.pay_col.addWidget(muted("لا توجد مدفوعات اليوم."))

        self._clear(self.top_col)
        for name, qty in top_month:
            row = QHBoxLayout()
            row.addWidget(QLabel(name), 1)
            row.addWidget(muted(ltr(str(qty))))
            w = QWidget()
            w.setLayout(row)
            self.top_col.addWidget(w)
        if not top_month:
            self.top_col.addWidget(muted("لا توجد مبيعات هذا الشهر بعد."))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Defer reload until after Qt finishes painting the frame, so the window is
        # responsive before the DB queries run. singleShot(0) posts to the event loop.
        self._show_timer.start(0)

    def hideEvent(self, event) -> None:
        self._show_timer.stop()
        super().hideEvent(event)

    @staticmethod
    def _clear(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
