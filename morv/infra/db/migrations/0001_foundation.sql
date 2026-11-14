-- 0001 — الأساس
-- علامة البرنامج، والإعدادات، ودليل الحسابات، وسجل النسخ الاحتياطي.
-- المبالغ في كل الجداول القادمة INTEGER بالقرش. ممنوع REAL.
-- لا يُعدَّل هذا الملف بعد دمجه؛ أي تغيير يكون في ملف ترقية جديد.

-- علامة تثبت أن الملف قاعدة Morv Tires (يفحصها الكود قبل فتح أي ملف)
CREATE TABLE app_meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
INSERT INTO app_meta (k, v) VALUES ('app_id', 'morv_tires');

-- إعدادات البرنامج: مفتاح ونص. تُضاف المفاتيح عند استخدامها فعلًا
CREATE TABLE settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);

-- دليل الحسابات: خمس مجموعات، ترميز هرمي (1 ثم 11 ثم 1110).
-- الحساب الرباعي فقط يقبل القيود (postable=1)، والمجموعات للتجميع في التقارير.
CREATE TABLE accounts (
  code        TEXT PRIMARY KEY CHECK (code GLOB '[1-5]*' AND length(code) IN (1, 2, 4)),
  name        TEXT NOT NULL,
  parent_code TEXT REFERENCES accounts(code),
  kind        TEXT NOT NULL CHECK (kind IN ('asset', 'liability', 'equity', 'revenue', 'expense')),
  normal_side TEXT NOT NULL CHECK (normal_side IN ('debit', 'credit')),
  postable    INTEGER NOT NULL CHECK (postable IN (0, 1)),
  system      INTEGER NOT NULL DEFAULT 1 CHECK (system IN (0, 1)),
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  CHECK ((length(code) = 4) = (postable = 1)),
  CHECK (parent_code IS NULL OR substr(code, 1, length(parent_code)) = parent_code)
);

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('1',    'الأصول',                         NULL, 'asset',     'debit',  0),
  ('11',   'النقدية والعهد',                 '1',  'asset',     'debit',  0),
  ('1110', 'الصندوق',                        '11', 'asset',     'debit',  1),
  ('1120', 'العهد',                          '11', 'asset',     'debit',  1),
  ('12',   'البنوك والمحافظ',                '1',  'asset',     'debit',  0),
  ('1210', 'بنك التحويلات',                  '12', 'asset',     'debit',  1),
  ('1220', 'بنك البطاقات',                   '12', 'asset',     'debit',  1),
  ('1230', 'المحافظ الإلكترونية',            '12', 'asset',     'debit',  1),
  ('13',   'الذمم المدينة',                  '1',  'asset',     'debit',  0),
  ('1310', 'العملاء',                        '13', 'asset',     'debit',  1),
  ('1320', 'عملاء التقسيط',                  '13', 'asset',     'debit',  1),
  ('14',   'المخزون',                        '1',  'asset',     'debit',  0),
  ('1410', 'مخزون الإطارات والبطاريات',      '14', 'asset',     'debit',  1),
  ('1420', 'مخزون البطاريات القديمة',        '14', 'asset',     'debit',  1),
  ('1430', 'مخزون معيب بانتظار المورد',      '14', 'asset',     'debit',  1),
  ('15',   'أصول متداولة أخرى',              '1',  'asset',     'debit',  0),
  ('1510', 'مصروفات مقدمة',                  '15', 'asset',     'debit',  1),
  ('16',   'الأصول الثابتة',                 '1',  'asset',     'debit',  0),
  ('1610', 'الأصول الثابتة',                 '16', 'asset',     'debit',  1),
  ('2',    'الالتزامات',                     NULL, 'liability', 'credit', 0),
  ('21',   'الموردون',                       '2',  'liability', 'credit', 0),
  ('2110', 'الموردون',                       '21', 'liability', 'credit', 1),
  ('23',   'المستحقات',                      '2',  'liability', 'credit', 0),
  ('2310', 'مصروفات مستحقة',                 '23', 'liability', 'credit', 1),
  ('24',   'الإيرادات المؤجلة',              '2',  'liability', 'credit', 0),
  ('2410', 'فوائد تقسيط مؤجلة',              '24', 'liability', 'credit', 1),
  ('3',    'حقوق الملكية',                   NULL, 'equity',    'credit', 0),
  ('3100', 'رأس المال',                      '3',  'equity',    'credit', 1),
  ('3200', 'الأرباح المرحلة',                '3',  'equity',    'credit', 1),
  ('3300', 'جاري المالك والمسحوبات',         '3',  'equity',    'debit',  1),
  ('3400', 'نتيجة السنة',                    '3',  'equity',    'credit', 1),
  ('4',    'الإيرادات',                      NULL, 'revenue',   'credit', 0),
  ('41',   'إيرادات النشاط',                 '4',  'revenue',   'credit', 0),
  ('4110', 'مبيعات الإطارات',                '41', 'revenue',   'credit', 1),
  ('4120', 'مبيعات البطاريات',               '41', 'revenue',   'credit', 1),
  ('4130', 'الخدمات والمصنعية',              '41', 'revenue',   'credit', 1),
  ('4140', 'إيراد فوائد التقسيط',            '41', 'revenue',   'credit', 1),
  ('4150', 'مبيعات البطاريات القديمة',       '41', 'revenue',   'credit', 1),
  ('4190', 'مردودات المبيعات',               '41', 'revenue',   'debit',  1),
  ('42',   'إيرادات أخرى',                   '4',  'revenue',   'credit', 0),
  ('4210', 'إيرادات متنوعة',                 '42', 'revenue',   'credit', 1),
  ('5',    'المصروفات',                      NULL, 'expense',   'debit',  0),
  ('51',   'تكلفة المبيعات',                 '5',  'expense',   'debit',  0),
  ('5110', 'تكلفة الإطارات',                 '51', 'expense',   'debit',  1),
  ('5120', 'تكلفة البطاريات',                '51', 'expense',   'debit',  1),
  ('5130', 'تكلفة البطاريات القديمة المباعة', '51', 'expense',   'debit',  1),
  ('5140', 'تكلفة الخدمات',                  '51', 'expense',   'debit',  1),
  ('52',   'المصروفات التشغيلية',            '5',  'expense',   'debit',  0),
  ('5210', 'الإيجار',                        '52', 'expense',   'debit',  1),
  ('5220', 'الكهرباء',                       '52', 'expense',   'debit',  1),
  ('5230', 'الرواتب',                        '52', 'expense',   'debit',  1),
  ('5240', 'النقل',                          '52', 'expense',   'debit',  1),
  ('5250', 'الصيانة',                        '52', 'expense',   'debit',  1),
  ('5260', 'مصروفات بنكية',                  '52', 'expense',   'debit',  1),
  ('5270', 'فروق الجرد',                     '52', 'expense',   'debit',  1),
  ('5280', 'تكلفة الضمان',                   '52', 'expense',   'debit',  1),
  ('5290', 'مصروفات تشغيلية أخرى',           '52', 'expense',   'debit',  1);

-- سجل كل نسخة احتياطية وكل اختبار استعادة، ناجحًا أو فاشلًا
CREATE TABLE backup_runs (
  id          INTEGER PRIMARY KEY,
  action      TEXT NOT NULL CHECK (action IN ('backup', 'restore_test')),
  kind        TEXT NOT NULL CHECK (kind IN ('manual', 'daily', 'pre_migration', 'safety')),
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  file_name   TEXT,
  size_bytes  INTEGER CHECK (size_bytes IS NULL OR size_bytes >= 0),
  sha256      TEXT,
  status      TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
  detail      TEXT
);
CREATE INDEX backup_runs_time ON backup_runs(started_at DESC);
