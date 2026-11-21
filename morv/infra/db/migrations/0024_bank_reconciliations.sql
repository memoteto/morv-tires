-- Migration 0024: bank and wallet reconciliation records; a record posts no journal entry.
-- The program balance is frozen at save time. Records are immutable and a cancel is a second row.

CREATE TABLE bank_reconciliations (
  id                INTEGER PRIMARY KEY,
  number            TEXT NOT NULL UNIQUE,
  request_key       TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash      TEXT NOT NULL,
  kind              TEXT NOT NULL CHECK (kind IN ('reconciliation', 'cancel')),
  account_code      TEXT NOT NULL CHECK (account_code IN ('1210', '1220', '1230')) REFERENCES accounts(code),
  statement_date    TEXT NOT NULL CHECK (statement_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  statement_balance INTEGER NOT NULL,
  program_balance   INTEGER NOT NULL,
  difference        INTEGER NOT NULL,
  reference         TEXT,
  memo              TEXT,
  cancels_id        INTEGER UNIQUE REFERENCES bank_reconciliations(id),
  user_id           INTEGER REFERENCES users(id),
  at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'cancel') = (cancels_id IS NOT NULL)),
  CHECK (difference = statement_balance - program_balance),
  CHECK (kind = 'cancel' OR difference = 0 OR length(trim(coalesce(memo, ''))) > 0),
  CHECK (kind = 'reconciliation' OR length(trim(coalesce(memo, ''))) > 0)
);

CREATE TRIGGER bank_reconciliations_one_per_date BEFORE INSERT ON bank_reconciliations
WHEN NEW.kind = 'reconciliation' AND EXISTS (
  SELECT 1 FROM bank_reconciliations r
   WHERE r.kind = 'reconciliation' AND r.account_code = NEW.account_code AND r.statement_date = NEW.statement_date
     AND NOT EXISTS (SELECT 1 FROM bank_reconciliations x WHERE x.cancels_id = r.id))
BEGIN SELECT RAISE(ABORT, 'هذا الحساب مطابَق في هذا التاريخ بالفعل'); END;

CREATE TRIGGER bank_reconciliations_no_update BEFORE UPDATE ON bank_reconciliations
BEGIN SELECT RAISE(ABORT, 'محضر مطابقة محفوظ: لا تعديل؛ الإلغاء بمستند ثانٍ'); END;
CREATE TRIGGER bank_reconciliations_no_delete BEFORE DELETE ON bank_reconciliations
BEGIN SELECT RAISE(ABORT, 'محضر مطابقة محفوظ: لا حذف؛ الإلغاء بمستند ثانٍ'); END;
