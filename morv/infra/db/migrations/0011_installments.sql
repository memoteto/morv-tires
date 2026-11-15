-- الترقية 0011: عقد التقسيط وجدول أقساطه. العقد المعتمد مستند مالي لا يُعدَّل ولا يُحذف.
-- الفاتورة تُرحَّل أولًا بوسيلة دفع 'installment' فيقع كامل السعر على 1320، ثم يحصّل العقد
-- المقدم ويضيف الفائدة إلى 1320 مقابل الفوائد المؤجلة 2410 (morv/infra/installments.py).

CREATE TABLE installment_contracts (
  id                INTEGER PRIMARY KEY,
  number            TEXT NOT NULL UNIQUE,
  request_key       TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash      TEXT NOT NULL,
  sale_id           INTEGER NOT NULL UNIQUE REFERENCES sales(id),
  customer_id       INTEGER NOT NULL REFERENCES customers(id),
  contract_date     TEXT NOT NULL CHECK (contract_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  cash_price        INTEGER NOT NULL CHECK (cash_price > 0),
  down              INTEGER NOT NULL CHECK (down >= 0),
  financed          INTEGER NOT NULL CHECK (financed > 0),
  rate_bp           INTEGER NOT NULL CHECK (rate_bp >= 0 AND rate_bp <= 10000),
  markup            INTEGER NOT NULL CHECK (markup >= 0),
  months            INTEGER NOT NULL CHECK (months >= 2 AND months <= 24),
  instalments_total INTEGER NOT NULL CHECK (instalments_total > 0),
  contract_total    INTEGER NOT NULL CHECK (contract_total > 0),
  first_due         TEXT NOT NULL CHECK (first_due GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  down_method       TEXT CHECK (down_method IS NULL OR down_method IN ('cash', 'card', 'transfer', 'wallet')),
  national_id       TEXT NOT NULL CHECK (length(national_id) = 14),
  address           TEXT,
  work_place        TEXT,
  whatsapp_ok       INTEGER NOT NULL DEFAULT 0 CHECK (whatsapp_ok IN (0, 1)),
  guarantor_name    TEXT,
  guarantor_mobile  TEXT,
  guarantor_nid     TEXT,
  guarantor_relation TEXT,
  notes             TEXT,
  journal_entry_id  INTEGER UNIQUE REFERENCES journal_entries(id),
  user_id           INTEGER REFERENCES users(id),
  at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK (financed = cash_price - down),
  CHECK (instalments_total = financed + markup),
  CHECK (contract_total = down + instalments_total),
  CHECK ((down = 0) = (down_method IS NULL))
);
CREATE INDEX installment_contracts_customer ON installment_contracts(customer_id);

CREATE TABLE installment_rows (
  id            INTEGER PRIMARY KEY,
  contract_id   INTEGER NOT NULL REFERENCES installment_contracts(id),
  no            INTEGER NOT NULL CHECK (no > 0),
  due_date      TEXT NOT NULL CHECK (due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  principal     INTEGER NOT NULL CHECK (principal > 0),
  markup        INTEGER NOT NULL CHECK (markup >= 0),
  amount        INTEGER NOT NULL CHECK (amount > 0),
  balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
  UNIQUE (contract_id, no),
  CHECK (amount = principal + markup)
);
CREATE INDEX installment_rows_due ON installment_rows(due_date);

CREATE TRIGGER installment_contracts_no_update BEFORE UPDATE ON installment_contracts BEGIN SELECT RAISE(ABORT, 'عقد معتمد: لا تعديل'); END;
CREATE TRIGGER installment_contracts_no_delete BEFORE DELETE ON installment_contracts BEGIN SELECT RAISE(ABORT, 'عقد معتمد: لا حذف'); END;
CREATE TRIGGER installment_rows_no_update BEFORE UPDATE ON installment_rows BEGIN SELECT RAISE(ABORT, 'جدول عقد معتمد: لا تعديل'); END;
CREATE TRIGGER installment_rows_no_delete BEFORE DELETE ON installment_rows BEGIN SELECT RAISE(ABORT, 'جدول عقد معتمد: لا حذف'); END;

-- إعادة بناء دفعات الفاتورة لتقبل وسيلة 'installment' التي تضع السعر على حساب عملاء التقسيط.
ALTER TABLE sale_payments RENAME TO sale_payments_old;
CREATE TABLE sale_payments (
  id        INTEGER PRIMARY KEY,
  sale_id   INTEGER NOT NULL REFERENCES sales(id),
  method    TEXT NOT NULL CHECK (method IN ('cash', 'card', 'transfer', 'wallet', 'credit', 'installment')),
  amount    INTEGER NOT NULL CHECK (amount > 0),
  received  INTEGER CHECK (received IS NULL OR received >= amount),
  reference TEXT,
  CHECK ((method = 'cash') = (received IS NOT NULL))
);
INSERT INTO sale_payments (id, sale_id, method, amount, received, reference)
  SELECT id, sale_id, method, amount, received, reference FROM sale_payments_old;
DROP TABLE sale_payments_old;
CREATE TRIGGER sale_payments_no_update BEFORE UPDATE ON sale_payments BEGIN SELECT RAISE(ABORT, 'دفعات فاتورة مرحّلة: لا تعديل'); END;
CREATE TRIGGER sale_payments_no_delete BEFORE DELETE ON sale_payments BEGIN SELECT RAISE(ABORT, 'دفعات فاتورة مرحّلة: لا حذف'); END;

-- صلاحية العقود للمديرين الموجودين قبل هذه الترقية (الجدد تأخذها من قالب الدور).
INSERT OR IGNORE INTO user_permissions (user_id, perm, allowed)
  SELECT id, 'installments.manage', 1 FROM users WHERE role = 'manager';
