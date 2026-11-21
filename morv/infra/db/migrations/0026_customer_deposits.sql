-- Migration 0026: customer deposits (liability 2340): deposit, refund, and application against credit debt.
-- Documents are immutable; a reversal is a second row. Only 'apply' and its reversal touch account 1310.

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('2340', 'عربون العملاء', '23', 'liability', 'credit', 1);

CREATE TABLE customer_deposits (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  customer_id      INTEGER NOT NULL REFERENCES customers(id),
  kind             TEXT NOT NULL CHECK (kind IN ('deposit', 'refund', 'apply', 'reversal')),
  doc_date         TEXT NOT NULL CHECK (doc_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  method           TEXT CHECK (method IS NULL OR method IN ('cash', 'card', 'transfer', 'wallet')),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  notes            TEXT,
  reverses_id      INTEGER UNIQUE REFERENCES customer_deposits(id),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
  CHECK (kind <> 'apply' OR method IS NULL),
  CHECK (kind NOT IN ('deposit', 'refund') OR method IS NOT NULL)
);

CREATE INDEX idx_customer_deposits_customer ON customer_deposits(customer_id);

CREATE TRIGGER customer_deposits_no_update BEFORE UPDATE ON customer_deposits
BEGIN SELECT RAISE(ABORT, 'مستند عربون مرحّل: لا تعديل؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER customer_deposits_no_delete BEFORE DELETE ON customer_deposits
BEGIN SELECT RAISE(ABORT, 'مستند عربون مرحّل: لا حذف؛ التصحيح بمستند عكسي'); END;
