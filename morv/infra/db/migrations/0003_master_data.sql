-- الترقية 0003: البيانات الأساسية — العملاء ومركباتهم، الموردون، الأصناف وحقولها.
-- (المرحلة 2)
-- الأرصدة الافتتاحية والدفعات والحركات تأتي في المرحلة 3 مع فاتورة الشراء.

-- العملاء. الموبايل فريد (قرار المالك: يُرفض التكرار). name_norm للبحث العربي الموحّد.
CREATE TABLE customers (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL CHECK (length(trim(name)) > 0),
  name_norm     TEXT NOT NULL,
  mobile        TEXT NOT NULL UNIQUE CHECK (mobile GLOB '01[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
  mobile2       TEXT,
  national_id   TEXT CHECK (national_id IS NULL OR length(national_id) = 14),
  customer_type TEXT NOT NULL DEFAULT 'individual'
                CHECK (customer_type IN ('individual', 'workshop', 'company', 'trader')),
  price_level   TEXT NOT NULL DEFAULT 'retail' CHECK (price_level IN ('retail', 'wholesale', 'super')),
  credit_limit  INTEGER NOT NULL DEFAULT 0 CHECK (credit_limit >= 0),   -- بالقرش؛ 0 = لا آجل
  credit_days   INTEGER NOT NULL DEFAULT 0 CHECK (credit_days >= 0),
  address       TEXT,
  work_place    TEXT,
  tax_id        TEXT,
  notes         TEXT,
  whatsapp_ok   INTEGER NOT NULL DEFAULT 0 CHECK (whatsapp_ok IN (0, 1)),
  active        INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  updated_at    TEXT
);
CREATE INDEX customers_name_norm ON customers(name_norm);

-- مركبات العميل: يُبحث عنها برقم اللوحة، وتُقترح مقاساتها في البيع لاحقًا.
CREATE TABLE customer_vehicles (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
  make        TEXT,
  model       TEXT,
  year        INTEGER CHECK (year IS NULL OR (year BETWEEN 1950 AND 2100)),
  plate       TEXT,
  plate_norm  TEXT,
  tire_front  TEXT,
  tire_rear   TEXT,
  battery     TEXT,
  odometer    INTEGER CHECK (odometer IS NULL OR odometer >= 0)
);
CREATE INDEX customer_vehicles_plate ON customer_vehicles(plate_norm);
CREATE INDEX customer_vehicles_customer ON customer_vehicles(customer_id);

-- الموردون. الموبايل فريد أيضًا.
CREATE TABLE suppliers (
  id             INTEGER PRIMARY KEY,
  name           TEXT NOT NULL CHECK (length(trim(name)) > 0),
  name_norm      TEXT NOT NULL,
  mobile         TEXT NOT NULL UNIQUE CHECK (mobile GLOB '01[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'),
  phone2         TEXT,
  contact_person TEXT,
  address        TEXT,
  tax_id         TEXT,
  notes          TEXT,
  active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  updated_at     TEXT
);
CREATE INDEX suppliers_name_norm ON suppliers(name_norm);

-- الأصناف: الأعمدة الثابتة التي يعتمد عليها البيع والبحث. بقية الخصائص في item_attrs.
CREATE TABLE items (
  id              INTEGER PRIMARY KEY,
  kind            TEXT NOT NULL CHECK (kind IN ('tire', 'battery', 'service')),
  code            TEXT NOT NULL UNIQUE COLLATE NOCASE CHECK (length(trim(code)) > 0),
  barcode         TEXT UNIQUE CHECK (barcode IS NULL OR length(barcode) >= 4),
  name            TEXT NOT NULL CHECK (length(trim(name)) > 0),
  name_norm       TEXT NOT NULL,
  brand           TEXT,
  model           TEXT,
  price_retail    INTEGER NOT NULL CHECK (price_retail > 0),            -- بالقرش
  price_wholesale INTEGER NOT NULL CHECK (price_wholesale > 0),
  price_super     INTEGER NOT NULL CHECK (price_super > 0),
  min_qty         INTEGER NOT NULL DEFAULT 0 CHECK (min_qty >= 0),
  notes           TEXT,
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  updated_at      TEXT
);
CREATE INDEX items_name_norm ON items(name_norm);
CREATE INDEX items_kind ON items(kind, active);

-- خصائص الصنف حسب نوعه (المقاس، الأمبير...) كقيم نصية بمفتاح الحقل.
CREATE TABLE item_attrs (
  item_id   INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  field_key TEXT NOT NULL,
  value     TEXT NOT NULL,
  PRIMARY KEY (item_id, field_key)
);
CREATE INDEX item_attrs_key_value ON item_attrs(field_key, value);

-- إعداد حقول بطاقة الصنف: أي حقل يظهر، وأيها إلزامي، وأيها يُطبع، وأيها فلتر بحث.
-- builtin=1 للحقول المقفلة التي يعتمد عليها البرنامج فلا تُخفى. kind='common' للمشترك.
CREATE TABLE item_fields (
  kind       TEXT NOT NULL CHECK (kind IN ('common', 'tire', 'battery', 'service')),
  key        TEXT NOT NULL,
  label      TEXT NOT NULL,
  example    TEXT,
  ftype      TEXT NOT NULL CHECK (ftype IN ('text', 'num', 'sel', 'bool', 'size')),
  options    TEXT,                       -- خيارات القائمة مفصولة بـ |
  show       INTEGER NOT NULL DEFAULT 1 CHECK (show IN (0, 1)),
  required   INTEGER NOT NULL DEFAULT 0 CHECK (required IN (0, 1)),
  print      INTEGER NOT NULL DEFAULT 0 CHECK (print IN (0, 1)),
  filter     INTEGER NOT NULL DEFAULT 0 CHECK (filter IN (0, 1)),
  builtin    INTEGER NOT NULL DEFAULT 0 CHECK (builtin IN (0, 1)),
  sort_order INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (kind, key)
);

-- الحقول الافتراضية (من التصميم المعتمد). الأعمدة الثابتة في items لها مفاتيحها هنا أيضًا
-- حتى تُضبط إلزاميتها وظهورها من نفس الشاشة.
INSERT INTO item_fields (kind, key, label, example, ftype, options, show, required, print, filter, builtin, sort_order) VALUES
 ('common','name','اسم الصنف','إطار ميشلان Primacy 4','text',NULL,1,1,1,0,1,1),
 ('common','code','الكود الداخلي','TR-2055516-MI','text',NULL,1,1,1,0,1,2),
 ('common','price_retail','سعر القطاعي','6,450.00','num',NULL,1,1,0,0,1,3),
 ('common','brand','الماركة','Michelin','text',NULL,1,1,1,1,0,4),
 ('common','model','الموديل / النقشة','Primacy 4','text',NULL,1,0,0,0,0,5),
 ('common','barcode','الباركود','6221234500011','text',NULL,1,0,0,0,0,6),
 ('common','origin','بلد المنشأ','كوريا','text',NULL,1,0,0,1,0,7),
 ('common','price_wholesale','سعر الجملة','6,120.00','num',NULL,1,0,0,0,0,8),
 ('common','price_super','سعر جملة الجملة','5,950.00','num',NULL,1,0,0,0,0,9),
 ('common','min_qty','حد الطلب','8','num',NULL,1,0,0,0,0,10),
 ('common','loc','مكان التخزين','رف A3','text',NULL,1,0,0,0,0,11),
 ('common','war','مدة الضمان (شهر)','12','num',NULL,1,0,1,0,0,12),
 ('common','cond','الحالة','جديد / مستعمل','sel','جديد|مستعمل',1,0,0,1,0,13),
 ('common','notes','ملاحظات','—','text',NULL,1,0,0,0,0,14),
 ('tire','size','المقاس','205/55R16','size',NULL,1,1,1,1,1,1),
 ('tire','li','مؤشر الحمل','91','text',NULL,1,1,1,0,0,2),
 ('tire','si','رمز السرعة','V','sel','H|T|V|W|Y|Q|R|S|L|N',1,1,1,0,0,3),
 ('tire','cat','الفئة','ملاكي','sel','ملاكي|دفع رباعي|نقل خفيف|نقل ثقيل',1,1,0,1,0,4),
 ('tire','cons','الإنشاء','Radial','sel','Radial|عادي (Bias)',1,1,0,0,0,5),
 ('tire','xl','تحمّل إضافي (XL)','نعم / لا','bool',NULL,1,0,0,0,0,6),
 ('tire','pr','عدد الطبقات (PR)','14PR','text',NULL,1,0,0,0,0,7),
 ('tire','rf','Run-Flat','نعم / لا','bool',NULL,1,0,0,0,0,8),
 ('tire','tl','بدون أنبوبة (Tubeless)','نعم / لا','bool',NULL,1,0,0,0,0,9),
 ('tire','season','الموسم','كل المواسم','sel','كل المواسم|صيفي',1,0,0,0,0,10),
 ('tire','eufuel','كفاءة الوقود (الملصق الأوروبي)','A – E','text',NULL,0,0,0,0,0,11),
 ('tire','euwet','التماسك على المبتل','A – E','text',NULL,0,0,0,0,0,12),
 ('tire','eunoise','الضوضاء (dB)','70','num',NULL,0,0,0,0,0,13),
 ('tire','utqg','UTQG','500 AA A','text',NULL,0,0,0,0,0,14),
 ('tire','wall','لون الجدار','أسود','text',NULL,0,0,0,0,0,15),
 ('tire','tread','عمق المداس (مم)','8 — للمستعمل','num',NULL,1,0,0,0,0,16),
 ('tire','kmwar','ضمان المسافة (كم)','60,000','num',NULL,0,0,0,0,0,17),
 ('battery','volt','الجهد','12V','sel','12V|24V|6V',1,1,1,0,0,1),
 ('battery','ah','السعة (أمبير)','70','num',NULL,1,1,1,1,0,2),
 ('battery','cca','CCA','640','num',NULL,1,1,1,0,0,3),
 ('battery','tech','التقنية','EFB','sel','سائلة عادية|EFB|AGM|جل',1,1,0,1,0,4),
 ('battery','box','مقاس الصندوق','L3','text',NULL,1,1,0,1,0,5),
 ('battery','eq','الأسماء المكافئة','DIN H6 · BCI 48','text',NULL,1,0,0,0,0,6),
 ('battery','pol','القطبية','الموجب يمين','sel','الموجب يمين|الموجب يسار',1,1,1,0,0,7),
 ('battery','dims','الأبعاد (ط×ع×ار) مم','278×175×190','text',NULL,1,1,0,0,0,8),
 ('battery','rc','السعة الاحتياطية (دقيقة)','120','num',NULL,1,0,0,0,0,9),
 ('battery','term','نوع القطب','مخروطي قياسي','sel','مخروطي قياسي|آسيوي رفيع|جانبي',1,0,0,0,0,10),
 ('battery','hold','طريقة التثبيت','B13','text',NULL,0,0,0,0,0,11),
 ('battery','wt','الوزن (كجم)','19.2','num',NULL,0,0,0,0,0,12),
 ('battery','ss','مناسبة لسيارات Start-Stop','نعم / لا','bool',NULL,1,0,0,0,0,13),
 ('service','dur','مدة التنفيذ (دقيقة)','30','num',NULL,1,0,0,0,0,1),
 ('service','per','تُحسب لكل','سيارة','sel','سيارة|إطار',1,0,0,0,0,2);

-- الكاشير الموجود يحصل على صلاحية إضافة العملاء (الافتراضي الجديد لدوره).
INSERT INTO user_permissions (user_id, perm, allowed)
  SELECT id, 'customers.manage', 1 FROM users WHERE role = 'cashier';
