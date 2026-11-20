-- Migration 0018: daily cash closing for the till (1110) and the two accounts that carry a count difference.
-- A closing is immutable; cancelling is a second row (kind 'cancel') that points at the closing it voids.

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('5291', 'عجز الصندوق',                 '52', 'expense',   'debit',  1),
  ('2320', 'زيادة الصندوق قيد التحقيق',   '23', 'liability', 'credit', 1);

CREATE TABLE cash_closings (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('close', 'cancel')),
  closing_date     TEXT NOT NULL CHECK (closing_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  cutoff_line_id   INTEGER NOT NULL CHECK (cutoff_line_id >= 0),
  book_balance     INTEGER NOT NULL,
  counted          INTEGER NOT NULL CHECK (counted >= 0),
  difference       INTEGER NOT NULL,
  memo             TEXT,
  cancels_id       INTEGER UNIQUE REFERENCES cash_closings(id),
  journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'cancel') = (cancels_id IS NOT NULL)),
  CHECK (difference = counted - book_balance),
  CHECK (kind = 'cancel' OR (difference = 0) = (journal_entry_id IS NULL)),
  CHECK (kind = 'cancel' OR difference = 0 OR length(trim(coalesce(memo, ''))) > 0),
  CHECK (kind = 'close' OR length(trim(coalesce(memo, ''))) > 0)
);
CREATE INDEX cash_closings_date ON cash_closings(closing_date);

-- One live closing per date: a date can be closed again only after its closing was cancelled.
CREATE TRIGGER cash_closings_one_per_date BEFORE INSERT ON cash_closings
WHEN NEW.kind = 'close' AND EXISTS (
  SELECT 1 FROM cash_closings c
   WHERE c.kind = 'close' AND c.closing_date = NEW.closing_date
     AND NOT EXISTS (SELECT 1 FROM cash_closings x WHERE x.cancels_id = c.id))
BEGIN SELECT RAISE(ABORT, 'هذا التاريخ مُقفل بالفعل'); END;

CREATE TRIGGER cash_closings_no_update BEFORE UPDATE ON cash_closings
BEGIN SELECT RAISE(ABORT, 'إقفال مرحّل: لا تعديل؛ الإلغاء بمستند عكسي'); END;
CREATE TRIGGER cash_closings_no_delete BEFORE DELETE ON cash_closings
BEGIN SELECT RAISE(ABORT, 'إقفال مرحّل: لا حذف؛ الإلغاء بمستند عكسي'); END;
