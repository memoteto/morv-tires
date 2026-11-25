"""Model/View table models for the screens."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from morv.core.text import ltr
from morv.infra import backup, users


class AuditModel(QAbstractTableModel):
    HEADERS = ["الوقت", "المستخدم", "العملية", "التفاصيل"]
    PAGE = 200
    ACTIONS = {
        "login.ok": "دخول", "login.failed": "محاولة دخول فاشلة", "user.create": "إنشاء مستخدم",
        "user.activate": "تفعيل مستخدم", "user.deactivate": "إيقاف مستخدم", "user.role": "تغيير دور",
        "user.permission": "تعديل صلاحية", "user.password": "كلمة مرور", "setup.done": "الإعداد الأول",
        "backup.restore": "استعادة نسخة", "backup.password": "تغيير كلمة مرور النسخ",
        "backup.recovery_key": "مفتاح استرداد جديد", "backup.relink": "إعادة ربط الجهاز",
        "shop.save": "تعديل بيانات المحل", "settings.usb": "تغيير مجلد الفلاشة",
        "user.setup_resume": "استكمال الإعداد الأول",
        "items.fields": "تعديل حقول الصنف", "items.fields.add": "إضافة حقل للصنف",
        "item.create": "إضافة صنف", "item.update": "تعديل صنف", "item.delete": "حذف صنف",
        "item.activate": "تفعيل صنف", "item.deactivate": "إيقاف صنف",
        "customer.create": "إضافة عميل", "customer.update": "تعديل عميل", "customer.delete": "حذف عميل",
        "customer.activate": "تفعيل عميل", "customer.deactivate": "إيقاف عميل",
        "supplier.create": "إضافة مورد", "supplier.update": "تعديل مورد", "supplier.delete": "حذف مورد",
        "supplier.activate": "تفعيل مورد", "supplier.deactivate": "إيقاف مورد",
        "sale.post": "ترحيل بيع", "sale.return": "مرتجع بيع", "purchase.post": "ترحيل شراء",
        "purchase.return": "مرتجع شراء", "purchase.return.reverse": "عكس مرتجع شراء",
        "partner.add": "إضافة شريك", "partner.activate": "تفعيل شريك", "partner.deactivate": "إيقاف شريك",
        "treasury.movement": "حركة خزينة", "treasury.reverse": "عكس حركة خزينة",
        "opening.post": "اعتماد الأرصدة الافتتاحية", "opening.reverse": "عكس الأرصدة الافتتاحية",
        "installment.contract": "عقد تقسيط", "installment.payment": "تحصيل قسط",
        "installment.interest_recognise": "إثبات فوائد التقسيط",
        "installment.interest_reverse": "عكس إثبات فوائد التقسيط",
        "stock.adjust": "تسوية مخزون", "stock.adjust.reverse": "عكس تسوية مخزون",
        "profit.reserve": "تعديل احتياطي الأرباح", "profit.shares": "تحديد نسب الأرباح",
        "profit.distribute": "توزيع أرباح", "profit.reverse": "عكس توزيع أرباح",
        "period.lock": "إقفال فترة", "period.unlock": "فتح فترة",
        "receipt.post": "تحصيل من عميل", "receipt.reverse": "عكس تحصيل عميل",
        "deposit.deposit": "استلام دفعة مقدمة", "deposit.refund": "رد دفعة مقدمة",
        "deposit.apply": "تطبيق دفعة مقدمة",
        "bank.reconcile": "مطابقة بنك", "bank.reconcile.cancel": "إلغاء مطابقة بنك",
        "cash.close": "إقفال يومي", "cash.close.cancel": "إلغاء إقفال يومي",
        "safe.count": "جرد صندوق", "safe.count.cancel": "إلغاء جرد صندوق",
    }

    def __init__(self, con):
        super().__init__()
        self.con = con
        self.rows = []
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self.rows = users.audit_rows(self.con, self.PAGE)
        self.endResetModel()

    def load_more(self) -> int:
        if not self.rows:
            return 0
        more = users.audit_rows(self.con, self.PAGE, before_id=self.rows[-1][0])
        if more:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(more) - 1)
            self.rows.extend(more)
            self.endInsertRows()
        return len(more)

    def rowCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None) -> int:
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        _id, at, username, action, _entity, _eid, detail = self.rows[index.row()]
        if role == Qt.ItemDataRole.ToolTipRole:
            return (at, username or "—", action, detail or "")[index.column()]
        return (ltr(at), username or "—", self.ACTIONS.get(action, action), detail or "")[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class BackupsModel(QAbstractTableModel):
    HEADERS = ["التاريخ", "النوع", "الحجم", "التشفير"]

    def __init__(self):
        super().__init__()
        self.items: list[backup.BackupInfo] = []
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self.items = backup.list_backups()
        self.endResetModel()

    def rowCount(self, parent=None) -> int:
        return 0 if parent is not None and parent.isValid() else len(self.items)

    def columnCount(self, parent=None) -> int:
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        b = self.items[index.row()]
        size = f"{b.size_bytes / 1_048_576:.1f} MB" if b.size_bytes >= 1_048_576 else f"{b.size_bytes // 1024} KB"
        return (ltr(b.at.strftime("%Y-%m-%d %H:%M")), backup.KIND_LABELS.get(b.kind, b.kind), ltr(size),
                "مشفّرة" if b.encrypted else "غير مشفّرة")[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None
