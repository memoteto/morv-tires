"""قراءة الإعدادات وحفظها في جدول settings.

الواجهة لا تكتب SQL بنفسها؛ تستدعي هذه الدوال فقط.
المفاتيح المستخدمة حاليًا:
  ui.theme             'light' أو 'dark'
  ui.sidebar_collapsed '1' أو '0'
"""
import sqlite3


def get(con: sqlite3.Connection, key: str, default: str = "") -> str:
    """قيمة إعداد، أو القيمة الافتراضية إن لم يُحفظ بعد."""
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def put(con: sqlite3.Connection, key: str, value: str) -> None:
    """حفظ إعداد (إضافة أو تحديث) مع وقت التعديل."""
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=strftime('%Y-%m-%d %H:%M:%S','now','localtime')",
        (key, str(value)))
