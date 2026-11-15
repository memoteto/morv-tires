-- الترقية 0009: فاتورة البيع. الفاتورة المرحّلة لا تُعدَّل ولا تُحذف؛ التصحيح بمرتجع لاحقًا.
-- كل فاتورة بيع تُكتب مع قيدها وحركات صرفها في معاملة واحدة (morv/infra/sales.py).

CREATE TABLE sales (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  customer_id      INTEGER REFERENCES customers(id),
  sale_date        TEXT NOT NULL CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  subtotal         INTEGER NOT NULL CHECK (subtotal > 0),
  line_discount    INTEGER NOT NULL CHECK (line_discount >= 0),
  invoice_discount INTEGER NOT NULL CHECK (invoice_discount >= 0),
  total            INTEGER NOT NULL CHECK (total > 0),
  odometer         INTEGER CHECK (odometer IS NULL OR odometer >= 0),
  vehicle_plate    TEXT,
  notes            TEXT,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK (total = subtotal - line_discount - invoice_discount)
);

CREATE TABLE sale_lines (
  id            INTEGER PRIMARY KEY,
  sale_id       INTEGER NOT NULL REFERENCES sales(id),
  line_no       INTEGER NOT NULL CHECK (line_no > 0),
  item_id       INTEGER NOT NULL REFERENCES items(id),
  qty           INTEGER NOT NULL CHECK (qty > 0),
  unit_price    INTEGER NOT NULL CHECK (unit_price > 0),
  line_discount INTEGER NOT NULL DEFAULT 0 CHECK (line_discount >= 0),
  invoice_share INTEGER NOT NULL DEFAULT 0 CHECK (invoice_share >= 0),
  net_total     INTEGER NOT NULL CHECK (net_total >= 0),
  UNIQUE (sale_id, line_no),
  CHECK (net_total = qty * unit_price - line_discount - invoice_share)
);

CREATE TABLE sale_payments (
  id        INTEGER PRIMARY KEY,
  sale_id   INTEGER NOT NULL REFERENCES sales(id),
  method    TEXT NOT NULL CHECK (method IN ('cash', 'card', 'transfer', 'wallet', 'credit')),
  amount    INTEGER NOT NULL CHECK (amount > 0),
  received  INTEGER CHECK (received IS NULL OR received >= amount),
  reference TEXT,
  CHECK ((method = 'cash') = (received IS NOT NULL))
);

CREATE TRIGGER sales_no_update BEFORE UPDATE ON sales BEGIN SELECT RAISE(ABORT, 'فاتورة مرحّلة: لا تعديل، والتصحيح بمرتجع'); END;
CREATE TRIGGER sales_no_delete BEFORE DELETE ON sales BEGIN SELECT RAISE(ABORT, 'فاتورة مرحّلة: لا حذف، والتصحيح بمرتجع'); END;
CREATE TRIGGER sale_lines_no_update BEFORE UPDATE ON sale_lines BEGIN SELECT RAISE(ABORT, 'سطور فاتورة مرحّلة: لا تعديل'); END;
CREATE TRIGGER sale_lines_no_delete BEFORE DELETE ON sale_lines BEGIN SELECT RAISE(ABORT, 'سطور فاتورة مرحّلة: لا حذف'); END;
CREATE TRIGGER sale_payments_no_update BEFORE UPDATE ON sale_payments BEGIN SELECT RAISE(ABORT, 'دفعات فاتورة مرحّلة: لا تعديل'); END;
CREATE TRIGGER sale_payments_no_delete BEFORE DELETE ON sale_payments BEGIN SELECT RAISE(ABORT, 'دفعات فاتورة مرحّلة: لا حذف'); END;

-- صلاحية ترحيل البيع للمدير والكاشير الموجودين قبل هذه الترقية (الجدد تأخذها من قالب الدور).
INSERT OR IGNORE INTO user_permissions (user_id, perm, allowed)
  SELECT id, 'sales.post', 1 FROM users WHERE role IN ('manager', 'cashier');
