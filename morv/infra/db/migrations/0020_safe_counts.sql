-- Migration 0020: periodic safe (1130) counts, with their own shortage and overage accounts.
-- Same shape as cash_closings: immutable rows, one live count per date, cancel is a second row.

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('5292', 'عجز الخزنة',                 '52', 'expense',   'debit',  1),
  ('2330', 'زيادة الخزنة قيد التحقيق',   '23', 'liability', 'credit', 1);

CREATE TABLE safe_counts (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('count', 'cancel')),
  count_date       TEXT NOT NULL CHECK (count_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  cutoff_line_id   INTEGER NOT NULL CHECK (cutoff_line_id >= 0),
  book_balance     INTEGER NOT NULL,
  counted          INTEGER NOT NULL CHECK (counted >= 0),
  difference       INTEGER NOT NULL,
  memo             TEXT,
  cancels_id       INTEGER UNIQUE REFERENCES safe_counts(id),
  journal_entry_id INTEGER UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'cancel') = (cancels_id IS NOT NULL)),
  CHECK (difference = counted - book_balance),
  CHECK (kind = 'cancel' OR (difference = 0) = (journal_entry_id IS NULL)),
  CHECK (kind = 'cancel' OR difference = 0 OR length(trim(coalesce(memo, ''))) > 0),
  CHECK (kind = 'count' OR length(trim(coalesce(memo, ''))) > 0)
);

CREATE TRIGGER safe_counts_one_per_date BEFORE INSERT ON safe_counts
WHEN NEW.kind = 'count' AND EXISTS (
  SELECT 1 FROM safe_counts c
   WHERE c.kind = 'count' AND c.count_date = NEW.count_date
     AND NOT EXISTS (SELECT 1 FROM safe_counts x WHERE x.cancels_id = c.id))
BEGIN SELECT RAISE(ABORT, 'هذا التاريخ مجرود بالفعل'); END;

CREATE TRIGGER safe_counts_no_update BEFORE UPDATE ON safe_counts
BEGIN SELECT RAISE(ABORT, 'جرد مرحّل: لا تعديل؛ الإلغاء بمستند عكسي'); END;
CREATE TRIGGER safe_counts_no_delete BEFORE DELETE ON safe_counts
BEGIN SELECT RAISE(ABORT, 'جرد مرحّل: لا حذف؛ الإلغاء بمستند عكسي'); END;
