-- الترقية 0030: الأرصدة الافتتاحية (حسابات، شركاء، عملاء، موردون). قرار 45.
-- كل سطر يُرحَّل بقيد مستقل مقابل حساب انتقالي 3500 يُصفَّر عند اكتمال المستند، ليبقى لكل عميل ومورد قيده.

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('3500', 'أرصدة افتتاحية انتقالية', '3', 'equity', 'credit', 1);

CREATE TABLE opening_balances (
  id           INTEGER PRIMARY KEY,
  number       TEXT NOT NULL UNIQUE,
  request_key  TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('opening', 'reversal')),
  opening_date TEXT NOT NULL CHECK (opening_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  note         TEXT NOT NULL DEFAULT '',
  reverses_id  INTEGER UNIQUE REFERENCES opening_balances(id),
  user_id      INTEGER REFERENCES users(id),
  at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL))
);

CREATE TABLE opening_balance_lines (
  id               INTEGER PRIMARY KEY,
  opening_id       INTEGER NOT NULL REFERENCES opening_balances(id),
  line_no          INTEGER NOT NULL CHECK (line_no > 0),
  kind             TEXT NOT NULL CHECK (kind IN ('account', 'partner_capital', 'partner_current', 'retained',
                                                 'customer', 'supplier')),
  account          TEXT REFERENCES accounts(code),
  partner_id       INTEGER REFERENCES partners(id),
  customer_id      INTEGER REFERENCES customers(id),
  supplier_id      INTEGER REFERENCES suppliers(id),
  amount           INTEGER NOT NULL CHECK (amount <> 0),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  UNIQUE (opening_id, line_no),
  CHECK ((kind = 'account') = (account IS NOT NULL)),
  CHECK ((kind IN ('partner_capital', 'partner_current')) = (partner_id IS NOT NULL)),
  CHECK ((kind = 'customer') = (customer_id IS NOT NULL)),
  CHECK ((kind = 'supplier') = (supplier_id IS NOT NULL)),
  CHECK (kind NOT IN ('customer', 'supplier', 'partner_capital') OR amount > 0)
);
CREATE INDEX opening_balance_lines_customer ON opening_balance_lines(customer_id);
CREATE INDEX opening_balance_lines_supplier ON opening_balance_lines(supplier_id);

-- السطور السارية: سطور مستند افتتاحي لم يُعكس.
CREATE VIEW active_opening_lines AS
SELECT l.*, o.opening_date FROM opening_balance_lines l JOIN opening_balances o ON o.id = l.opening_id
WHERE o.kind = 'opening' AND NOT EXISTS (SELECT 1 FROM opening_balances r WHERE r.reverses_id = o.id);

CREATE TRIGGER opening_balances_no_update BEFORE UPDATE ON opening_balances
BEGIN SELECT RAISE(ABORT, 'رصيد افتتاحي مرحّل: لا تعديل؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER opening_balances_no_delete BEFORE DELETE ON opening_balances
BEGIN SELECT RAISE(ABORT, 'رصيد افتتاحي مرحّل: لا حذف؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER opening_balance_lines_no_update BEFORE UPDATE ON opening_balance_lines
BEGIN SELECT RAISE(ABORT, 'سطور رصيد افتتاحي مرحّل: لا تعديل'); END;
CREATE TRIGGER opening_balance_lines_no_delete BEFORE DELETE ON opening_balance_lines
BEGIN SELECT RAISE(ABORT, 'سطور رصيد افتتاحي مرحّل: لا حذف'); END;
