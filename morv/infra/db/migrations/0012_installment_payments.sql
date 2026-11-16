-- الترقية 0012: تحصيل أقساط عقود التقسيط. السداد مستند مستقل عن installment_rows الثابت؛
-- كل دفعة توزَّع على الأسطر غير المسدَّدة بالأقدم استحقاقًا أولًا (morv/infra/installments.py: collect_payment).

CREATE TABLE installment_payments (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  contract_id      INTEGER NOT NULL REFERENCES installment_contracts(id),
  payment_date     TEXT NOT NULL CHECK (payment_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  method           TEXT NOT NULL CHECK (method IN ('cash', 'card', 'transfer', 'wallet')),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  notes            TEXT,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
CREATE INDEX installment_payments_contract ON installment_payments(contract_id);

CREATE TABLE installment_payment_lines (
  id          INTEGER PRIMARY KEY,
  payment_id  INTEGER NOT NULL REFERENCES installment_payments(id),
  row_id      INTEGER NOT NULL REFERENCES installment_rows(id),
  amount      INTEGER NOT NULL CHECK (amount > 0),
  UNIQUE (payment_id, row_id)
);
CREATE INDEX installment_payment_lines_row ON installment_payment_lines(row_id);

CREATE TRIGGER installment_payments_no_update BEFORE UPDATE ON installment_payments BEGIN SELECT RAISE(ABORT, 'دفعة قسط مرحّلة: لا تعديل'); END;
CREATE TRIGGER installment_payments_no_delete BEFORE DELETE ON installment_payments BEGIN SELECT RAISE(ABORT, 'دفعة قسط مرحّلة: لا حذف'); END;
CREATE TRIGGER installment_payment_lines_no_update BEFORE UPDATE ON installment_payment_lines BEGIN SELECT RAISE(ABORT, 'سطر دفعة مرحّل: لا تعديل'); END;
CREATE TRIGGER installment_payment_lines_no_delete BEFORE DELETE ON installment_payment_lines BEGIN SELECT RAISE(ABORT, 'سطر دفعة مرحّل: لا حذف'); END;
