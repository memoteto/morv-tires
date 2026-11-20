"""Read-only dialog listing unresolved shortages, overages and partners who owe the shop."""
from morv.core.money import fmt
from morv.core.text import ltr
from morv.infra import suspended_balances
from morv.ui.modal import Modal
from morv.ui.widgets import muted


class SuspendedBalancesDialog(Modal):
    def __init__(self, session, parent=None):
        super().__init__("الأرصدة المعلّقة", parent, width=620)
        self.report = r = suspended_balances.build(session.con)
        self.body.addWidget(muted("فروق بلا تحقيق مُسوًّى بعد، وشركاء جاريهم مدين. للقراءة فقط."))
        self._section(f"عجز: {ltr(fmt(r.shortage_total))} (مصروف يخفض الربح)", r.shortages)
        self._section(f"زيادة: {ltr(fmt(r.overage_total))} (التزام لا يدخل الربح)", r.overages)
        self.body.addWidget(muted("شركاء مدينون للمحل: " + ("لا يوجد" if not r.partner_debts else "")))
        for debt in r.partner_debts:
            self.body.addWidget(muted(f"{debt.name} · {ltr(fmt(debt.amount))}"))
        if r.shortage_drift or r.overage_drift:
            self.body.addWidget(muted("تحذير: مجموع المستندات لا يطابق الدفتر "
                                      f"(عجز {ltr(fmt(r.shortage_drift))}، زيادة {ltr(fmt(r.overage_drift))})"))

    def _section(self, title: str, items) -> None:
        self.body.addWidget(muted(title))
        for d in items[:15]:
            self.body.addWidget(muted(f"{d.source} {ltr(d.number)} · {ltr(d.day)} · {ltr(fmt(d.amount))} · {d.memo}"))
        if len(items) > 15:
            self.body.addWidget(muted(f"و{len(items) - 15} أخرى"))
