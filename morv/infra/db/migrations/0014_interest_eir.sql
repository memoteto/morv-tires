-- 0014: immutable per-contract EIR recognition using contractual ACT/365F cash flows.
-- Do not modify installment contracts, original payment allocations, or historic journal entries.

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
  UNIQUE (contract_id, period)
);
CREATE INDEX interest_recognitions_period ON interest_recognitions(period);

CREATE TRIGGER interest_recognitions_no_update BEFORE UPDATE ON interest_recognitions
  BEGIN SELECT RAISE(ABORT, 'اعتراف فوائد مرحّل: لا تعديل'); END;
CREATE TRIGGER interest_recognitions_no_delete BEFORE DELETE ON interest_recognitions
  BEGIN SELECT RAISE(ABORT, 'اعتراف فوائد مرحّل: لا حذف'); END;
