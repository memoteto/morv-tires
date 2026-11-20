-- Migration 0023: customer receipt vouchers that collect credit-sale debts (account 1310).
-- A receipt is immutable and its reversal is a second row; both post a ledger entry of source 'customer_receipt'.

CREATE TABLE customer_receipts (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  customer_id      INTEGER NOT NULL REFERENCES customers(id),
  kind             TEXT NOT NULL CHECK (kind IN ('receipt', 'reversal')),
  receipt_date     TEXT NOT NULL CHECK (receipt_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  method           TEXT NOT NULL CHECK (method IN ('cash', 'card', 'transfer', 'wallet')),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  notes            TEXT,
  reverses_id      INTEGER UNIQUE REFERENCES customer_receipts(id),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL))
);

CREATE INDEX idx_customer_receipts_customer ON customer_receipts(customer_id);

CREATE TRIGGER customer_receipts_no_update BEFORE UPDATE ON customer_receipts
BEGIN SELECT RAISE(ABORT, 'سند قبض مرحّل: لا تعديل؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER customer_receipts_no_delete BEFORE DELETE ON customer_receipts
BEGIN SELECT RAISE(ABORT, 'سند قبض مرحّل: لا حذف؛ التصحيح بمستند عكسي'); END;
