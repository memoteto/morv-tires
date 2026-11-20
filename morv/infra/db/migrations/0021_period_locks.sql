-- Migration 0021: financial period lock. Entries dated on or before the lock date are refused by the database.
-- A lock is immutable; unlocking is a second row (kind 'unlock') pointing at the lock it voids.

CREATE TABLE period_locks (
  id           INTEGER PRIMARY KEY,
  number       TEXT NOT NULL UNIQUE,
  request_key  TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('lock', 'unlock')),
  through_date TEXT NOT NULL CHECK (through_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  cancels_id   INTEGER UNIQUE REFERENCES period_locks(id),
  memo         TEXT,
  user_id      INTEGER REFERENCES users(id),
  at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'unlock') = (cancels_id IS NOT NULL)),
  CHECK (kind = 'lock' OR length(trim(coalesce(memo, ''))) > 0)
);

CREATE TRIGGER period_locks_no_update BEFORE UPDATE ON period_locks
BEGIN SELECT RAISE(ABORT, 'قفل الفترة مرحّل: لا تعديل؛ الفتح بمستند مستقل'); END;
CREATE TRIGGER period_locks_no_delete BEFORE DELETE ON period_locks
BEGIN SELECT RAISE(ABORT, 'قفل الفترة مرحّل: لا حذف؛ الفتح بمستند مستقل'); END;

CREATE TRIGGER journal_entries_period_locked BEFORE INSERT ON journal_entries
WHEN NEW.entry_date <= coalesce((
  SELECT max(l.through_date) FROM period_locks l
   WHERE l.kind = 'lock' AND NOT EXISTS (SELECT 1 FROM period_locks x WHERE x.cancels_id = l.id)), '')
BEGIN SELECT RAISE(ABORT, 'الفترة المالية مقفلة: لا يُرحَّل قيد بتاريخ داخلها'); END;
