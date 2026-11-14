"""نماذج الجداول (Model/View) للشاشات: سجل المراجعة وقائمة النسخ الاحتياطية.

القاعدة: الجدول لا يحمل عناصر داخل الصفوف؛ الأزرار خارج الجدول وتعمل على الصف المختار.
القيم اللاتينية (تواريخ وأحجام) تمر على ltr() حتى لا تنقلب داخل النص العربي.
"""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from morv.core.text import ltr
from morv.infra import backup, users


class AuditModel(QAbstractTableModel):
    """سجل المراجعة، يُحمَّل صفحة صفحة من الأحدث."""
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
        """تحميل أول صفحة."""
        super().__init__()
        self.con = con
        self.rows = []
        self.reload()

    def reload(self) -> None:
        """إعادة التحميل من البداية."""
        self.beginResetModel()
        self.rows = users.audit_rows(self.con, self.PAGE)
        self.endResetModel()

    def load_more(self) -> int:
        """تحميل صفحة أقدم وإلحاقها؛ يعيد عدد ما أُضيف."""
        if not self.rows:
            return 0
        more = users.audit_rows(self.con, self.PAGE, before_id=self.rows[-1][0])
        if more:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(more) - 1)
            self.rows.extend(more)
            self.endInsertRows()
        return len(more)

    def rowCount(self, parent=QModelIndex()) -> int:
        """عدد الصفوف المحمّلة."""
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        """أربعة أعمدة."""
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """نص الخلية."""
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        _id, at, username, action, _entity, _eid, detail = self.rows[index.row()]
        return (ltr(at), username or "—", self.ACTIONS.get(action, action), detail or "")[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """عناوين الأعمدة."""
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None


class BackupsModel(QAbstractTableModel):
    """ملفات النسخ في المجلد المحلي، الأحدث أولًا."""
    HEADERS = ["التاريخ", "النوع", "الحجم", "التشفير"]

    def __init__(self):
        """تحميل القائمة."""
        super().__init__()
        self.items: list[backup.BackupInfo] = []
        self.reload()

    def reload(self) -> None:
        """إعادة قراءة المجلد."""
        self.beginResetModel()
        self.items = backup.list_backups()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        """عدد النسخ."""
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()) -> int:
        """أربعة أعمدة."""
        return 4

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        """نص الخلية."""
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        b = self.items[index.row()]
        size = f"{b.size_bytes / 1_048_576:.1f} MB" if b.size_bytes >= 1_048_576 else f"{b.size_bytes // 1024} KB"
        return (ltr(b.at.strftime("%Y-%m-%d %H:%M")), backup.KIND_LABELS.get(b.kind, b.kind), ltr(size),
                "مشفّرة" if b.encrypted else "غير مشفّرة")[index.column()]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        """عناوين الأعمدة."""
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.HEADERS[section]
        return None
