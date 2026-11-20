-- Migration 0022: partner profit shares and profit distribution documents, plus the legal reserve account.
-- Shares are versioned sets effective from a date; a distribution is an immutable document and its reversal
-- is a second row. The entry moves equity only: it never touches cash, banks or the safe.

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('3250', 'الاحتياطي القانوني', '3', 'equity', 'credit', 1);

CREATE TABLE profit_share_sets (
  id             INTEGER PRIMARY KEY,
  number         TEXT NOT NULL UNIQUE,
  request_key    TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash   TEXT NOT NULL,
  effective_from TEXT NOT NULL CHECK (effective_from GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  memo           TEXT NOT NULL CHECK (length(trim(memo)) > 0),
  user_id        INTEGER REFERENCES users(id),
  at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);

CREATE TABLE profit_share_lines (
  set_id     INTEGER NOT NULL REFERENCES profit_share_sets(id),
  partner_id INTEGER NOT NULL REFERENCES partners(id),
  share_bp   INTEGER NOT NULL CHECK (share_bp > 0 AND share_bp <= 10000),
  PRIMARY KEY (set_id, partner_id)
);

CREATE TABLE profit_distributions (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN ('distribution', 'reversal')),
  from_date        TEXT NOT NULL,
  through_date     TEXT NOT NULL,
  period_net       INTEGER NOT NULL,
  reserve_bp       INTEGER NOT NULL CHECK (reserve_bp BETWEEN 0 AND 10000),
  reserve_amount   INTEGER NOT NULL CHECK (reserve_amount >= 0),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  share_set_id     INTEGER REFERENCES profit_share_sets(id),
  reverses_id      INTEGER UNIQUE REFERENCES profit_distributions(id),
  memo             TEXT NOT NULL CHECK (length(trim(memo)) > 0),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
  CHECK (kind = 'reversal' OR share_set_id IS NOT NULL)
);

CREATE TABLE profit_distribution_lines (
  distribution_id INTEGER NOT NULL REFERENCES profit_distributions(id),
  partner_id      INTEGER NOT NULL REFERENCES partners(id),
  share_bp        INTEGER NOT NULL,
  amount          INTEGER NOT NULL CHECK (amount >= 0),
  PRIMARY KEY (distribution_id, partner_id)
);

CREATE TRIGGER profit_share_sets_no_update BEFORE UPDATE ON profit_share_sets
BEGIN SELECT RAISE(ABORT, 'حصص الأرباح معتمدة: لا تعديل؛ تُسجَّل مجموعة جديدة'); END;
CREATE TRIGGER profit_share_sets_no_delete BEFORE DELETE ON profit_share_sets
BEGIN SELECT RAISE(ABORT, 'حصص الأرباح معتمدة: لا حذف'); END;
CREATE TRIGGER profit_share_lines_no_update BEFORE UPDATE ON profit_share_lines
BEGIN SELECT RAISE(ABORT, 'حصص الأرباح معتمدة: لا تعديل؛ تُسجَّل مجموعة جديدة'); END;
CREATE TRIGGER profit_share_lines_no_delete BEFORE DELETE ON profit_share_lines
BEGIN SELECT RAISE(ABORT, 'حصص الأرباح معتمدة: لا حذف'); END;
CREATE TRIGGER profit_distributions_no_update BEFORE UPDATE ON profit_distributions
BEGIN SELECT RAISE(ABORT, 'توزيع مرحّل: لا تعديل؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER profit_distributions_no_delete BEFORE DELETE ON profit_distributions
BEGIN SELECT RAISE(ABORT, 'توزيع مرحّل: لا حذف؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER profit_distribution_lines_no_update BEFORE UPDATE ON profit_distribution_lines
BEGIN SELECT RAISE(ABORT, 'توزيع مرحّل: لا تعديل'); END;
CREATE TRIGGER profit_distribution_lines_no_delete BEFORE DELETE ON profit_distribution_lines
BEGIN SELECT RAISE(ABORT, 'توزيع مرحّل: لا حذف'); END;
