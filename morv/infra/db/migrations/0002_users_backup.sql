-- الترقية 0002: المستخدمون والصلاحيات وسجل المراجعة، وتوسيع سجل النسخ الاحتياطي.
-- (المرحلة 1: الدخول والإعداد)

-- المستخدمون. كلمة المرور بصمة scrypt فقط ولا تُحفظ أبدًا بنصها.
CREATE TABLE users (
  id             INTEGER PRIMARY KEY,
  username       TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(username) >= 3),
  display_name   TEXT NOT NULL CHECK (length(trim(display_name)) > 0),
  role           TEXT NOT NULL CHECK (role IN ('manager', 'cashier')),
  pw_hash        TEXT NOT NULL,
  active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  must_change_pw INTEGER NOT NULL DEFAULT 0 CHECK (must_change_pw IN (0, 1)),
  failed_count   INTEGER NOT NULL DEFAULT 0 CHECK (failed_count >= 0),
  locked_until   TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  last_login_at  TEXT
);

-- صلاحية لكل مستخدم: تُنسخ من قالب الدور عند الإنشاء ويمكن تعديلها منفردة.
-- limit_value لحدود رقمية (مثل أقصى خصم) وتُستخدم عند بناء شاشاتها.
CREATE TABLE user_permissions (
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  perm        TEXT NOT NULL,
  allowed     INTEGER NOT NULL DEFAULT 1 CHECK (allowed IN (0, 1)),
  limit_value INTEGER,
  PRIMARY KEY (user_id, perm)
);

-- سجل المراجعة: كل عملية حساسة باسم من نفّذها ووقتها. لا يُحذف منه شيء.
CREATE TABLE audit_log (
  id        INTEGER PRIMARY KEY,
  at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  user_id   INTEGER REFERENCES users(id),
  username  TEXT,
  action    TEXT NOT NULL,
  entity    TEXT,
  entity_id TEXT,
  detail    TEXT
);
CREATE INDEX audit_log_time ON audit_log(at DESC);

-- سجل النسخ: إضافة الاستعادة الفعلية والنسخ إلى الفلاشة واسم المنفّذ.
-- SQLite لا يعدّل قيود CHECK، فيُعاد بناء الجدول بنقل البيانات.
CREATE TABLE backup_runs_new (
  id          INTEGER PRIMARY KEY,
  action      TEXT NOT NULL CHECK (action IN ('backup', 'restore_test', 'restore', 'usb_copy')),
  kind        TEXT NOT NULL CHECK (kind IN ('manual', 'daily', 'pre_migration', 'safety')),
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  file_name   TEXT,
  size_bytes  INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
  sha256      TEXT,
  status      TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
  detail      TEXT,
  username    TEXT
);
INSERT INTO backup_runs_new (id, action, kind, started_at, finished_at, file_name, size_bytes, sha256, status, detail)
  SELECT id, action, kind, started_at, finished_at, file_name, size_bytes, sha256, status, detail FROM backup_runs;
DROP TABLE backup_runs;
ALTER TABLE backup_runs_new RENAME TO backup_runs;
CREATE INDEX backup_runs_time ON backup_runs(started_at DESC);
