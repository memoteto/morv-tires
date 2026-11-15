-- المرحلة 4أ: فاتورة البيع (نقدي/تحويل/بطاقة/آجل). الإيراد والتحصيل في قيد واحد، والتكلفة من سجل الصرف.
-- عدد الأسطر والدفعات يُعلن في رأس الفاتورة، فيرفض المحفّز أي سطر زائد بعد الاكتمال.
-- مجموع الأسطر والدفعات وتطابق القيد تتحقق منه selling.reconcile_sales (CHECK لا يقارن بين جداول).

CREATE TABLE sales (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  customer_id      INTEGER REFERENCES customers(id),
  sale_date        TEXT NOT NULL CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  total            INTEGER NOT NULL CHECK (total > 0),
  paid             INTEGER NOT NULL CHECK (paid >= 0),
  credit           INTEGER NOT NULL CHECK (credit >= 0),
  line_count       INTEGER NOT NULL CHECK (line_count > 0),
  payment_count    INTEGER NOT NULL CHECK (payment_count >= 0),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  notes            TEXT,
  user_id          INTEGER REFERENCES users(id),
  posted_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK (paid + credit = total),
  CHECK (credit = 0 OR customer_id IS NOT NULL),
  CHECK ((paid > 0) = (payment_count > 0))
);
CREATE INDEX sales_customer ON sales(customer_id);

CREATE TABLE sale_lines (
  id         INTEGER PRIMARY KEY,
  sale_id    INTEGER NOT NULL REFERENCES sales(id),
  line_no    INTEGER NOT NULL CHECK (line_no > 0),
  item_id    INTEGER NOT NULL REFERENCES items(id),
  item_kind  TEXT NOT NULL CHECK (item_kind IN ('tire', 'battery', 'service')),
  qty        INTEGER NOT NULL CHECK (qty > 0),
  list_price INTEGER NOT NULL CHECK (list_price > 0),     -- السعر الافتراضي وقت البيع (حسب فئة العميل)
  unit_price INTEGER NOT NULL CHECK (unit_price > 0),
  line_total INTEGER NOT NULL CHECK (line_total = qty * unit_price),
  cost_total INTEGER NOT NULL CHECK (cost_total >= 0),    -- من طبقات FIFO؛ صفر للخدمة
  issue_id   INTEGER UNIQUE REFERENCES stock_issues(id),  -- الخدمة بلا صرف
  UNIQUE (sale_id, line_no),
  CHECK ((item_kind = 'service') = (issue_id IS NULL)),
  CHECK (item_kind <> 'service' OR cost_total = 0)
);
CREATE INDEX sale_lines_item ON sale_lines(item_id);

CREATE TABLE sale_payments (
  id      INTEGER PRIMARY KEY,
  sale_id INTEGER NOT NULL REFERENCES sales(id),
  method  TEXT NOT NULL CHECK (method IN ('cash', 'transfer', 'card')),
  amount  INTEGER NOT NULL CHECK (amount > 0),
  UNIQUE (sale_id, method)
);

CREATE TRIGGER sale_lines_limit BEFORE INSERT ON sale_lines
WHEN (SELECT count(*) FROM sale_lines WHERE sale_id = NEW.sale_id)
     >= coalesce((SELECT line_count FROM sales WHERE id = NEW.sale_id), 0)
BEGIN SELECT RAISE(ABORT, 'فاتورة مرحّلة: لا أسطر إضافية'); END;

CREATE TRIGGER sale_payments_limit BEFORE INSERT ON sale_payments
WHEN (SELECT count(*) FROM sale_payments WHERE sale_id = NEW.sale_id)
     >= coalesce((SELECT payment_count FROM sales WHERE id = NEW.sale_id), 0)
BEGIN SELECT RAISE(ABORT, 'فاتورة مرحّلة: لا دفعات إضافية'); END;

CREATE TRIGGER sales_no_update BEFORE UPDATE ON sales BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER sales_no_delete BEFORE DELETE ON sales BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER sale_lines_no_update BEFORE UPDATE ON sale_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER sale_lines_no_delete BEFORE DELETE ON sale_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER sale_payments_no_update BEFORE UPDATE ON sale_payments BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER sale_payments_no_delete BEFORE DELETE ON sale_payments BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;

-- الكاشير الحاليون يرثون صلاحية ترحيل البيع (قالب الدور الجديد لا يمس من أُنشئوا قبله)
INSERT OR IGNORE INTO user_permissions (user_id, perm, allowed)
SELECT id, 'sales.post', 1 FROM users WHERE role = 'cashier';
