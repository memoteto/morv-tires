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
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        _id, at, username, action, _entity, _eid, detail = self.rows[index.row()]
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
