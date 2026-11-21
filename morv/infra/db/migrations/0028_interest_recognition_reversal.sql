-- 0028: reversal of an interest recognition; a reversed month can be recognised again as a new generation.

ALTER TABLE interest_recognitions RENAME TO interest_recognitions_old;
CREATE TABLE interest_recognitions (
  id                 INTEGER PRIMARY KEY,
  contract_id        INTEGER NOT NULL REFERENCES installment_contracts(id),
  period             TEXT NOT NULL CHECK (period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
  entry_date         TEXT NOT NULL CHECK (entry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  amount             INTEGER NOT NULL CHECK (amount > 0),
  annual_rate        TEXT NOT NULL CHECK (length(annual_rate) > 0),
  schedule_hash      TEXT NOT NULL CHECK (length(schedule_hash) = 64),
  policy             TEXT NOT NULL DEFAULT 'ACT365F_V1' CHECK (policy = 'ACT365F_V1'),
  journal_entry_id   INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id            INTEGER NOT NULL REFERENCES users(id),
  at                 TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  generation         INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
  UNIQUE (contract_id, period, generation)
);
INSERT INTO interest_recognitions (id, contract_id, period, entry_date, amount, annual_rate, schedule_hash, policy,
                                   journal_entry_id, user_id, at)
  SELECT id, contract_id, period, entry_date, amount, annual_rate, schedule_hash, policy,
         journal_entry_id, user_id, at FROM interest_recognitions_old;
DROP TABLE interest_recognitions_old;
CREATE INDEX interest_recognitions_period ON interest_recognitions(period);
CREATE TRIGGER interest_recognitions_no_update BEFORE UPDATE ON interest_recognitions
  BEGIN SELECT RAISE(ABORT, 'اعتراف فوائد مرحّل: لا تعديل'); END;
CREATE TRIGGER interest_recognitions_no_delete BEFORE DELETE ON interest_recognitions
  BEGIN SELECT RAISE(ABORT, 'اعتراف فوائد مرحّل: لا حذف'); END;

CREATE TABLE interest_recognition_reversals (
  id               INTEGER PRIMARY KEY,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  recognition_id   INTEGER NOT NULL UNIQUE REFERENCES interest_recognitions(id),
  reversal_date    TEXT NOT NULL CHECK (reversal_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  reason           TEXT NOT NULL CHECK (length(trim(reason)) >= 3),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER NOT NULL REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
CREATE TRIGGER interest_recognition_reversals_no_update BEFORE UPDATE ON interest_recognition_reversals
  BEGIN SELECT RAISE(ABORT, 'عكس اعتراف فوائد مرحّل: لا تعديل'); END;
CREATE TRIGGER interest_recognition_reversals_no_delete BEFORE DELETE ON interest_recognition_reversals
  BEGIN SELECT RAISE(ABORT, 'عكس اعتراف فوائد مرحّل: لا حذف'); END;

CREATE VIEW active_interest_recognitions AS
  SELECT r.* FROM interest_recognitions r
  WHERE NOT EXISTS (SELECT 1 FROM interest_recognition_reversals v WHERE v.recognition_id = r.id);
