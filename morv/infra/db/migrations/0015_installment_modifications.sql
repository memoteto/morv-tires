-- 0015: immutable evidence for explicitly approved partial installment modifications.
-- No existing contracts, original instalments, prior payments, returns or journals are rewritten.
-- Posting is NOT enabled by this migration: no revised schedule may be inferred automatically.

CREATE TABLE installment_modifications (
  id                   INTEGER PRIMARY KEY,
  contract_id          INTEGER NOT NULL REFERENCES installment_contracts(id),
  return_id            INTEGER NOT NULL UNIQUE REFERENCES sale_returns(id),
  effective_date       TEXT NOT NULL CHECK (
    effective_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  agreement_reference  TEXT NOT NULL CHECK (length(trim(agreement_reference)) >= 8),
  classification       TEXT NOT NULL DEFAULT 'non_derecognition'
                       CHECK (classification = 'non_derecognition'),
  policy               TEXT NOT NULL DEFAULT 'ACT365F_MOD_V1'
                       CHECK (policy = 'ACT365F_MOD_V1'),
  original_annual_rate TEXT NOT NULL CHECK (length(trim(original_annual_rate)) > 0),
  carrying_before      INTEGER NOT NULL CHECK (carrying_before >= 0),
  original_future_pv   INTEGER NOT NULL CHECK (original_future_pv >= 0),
  modified_future_pv   INTEGER NOT NULL CHECK (modified_future_pv >= 0),
  modification_gain_loss INTEGER NOT NULL,
  received             INTEGER NOT NULL CHECK (received >= 0),
  approved_total_due   INTEGER NOT NULL CHECK (approved_total_due >= 0),
  applied_receipts     INTEGER NOT NULL CHECK (applied_receipts >= 0),
  refundable_excess    INTEGER NOT NULL CHECK (refundable_excess >= 0),
  remaining_claim      INTEGER NOT NULL CHECK (remaining_claim >= 0),
  schedule_hash        TEXT NOT NULL CHECK (length(schedule_hash) = 64),
  journal_entry_id     INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  approved_by          INTEGER NOT NULL REFERENCES users(id),
  at                   TEXT NOT NULL DEFAULT (
    strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK (abs(original_future_pv - carrying_before) <= 1),
  CHECK (modification_gain_loss = modified_future_pv - carrying_before),
  CHECK (received = applied_receipts + refundable_excess),
  CHECK (approved_total_due = applied_receipts + remaining_claim),
  CHECK (modified_future_pv <= remaining_claim)
);
CREATE INDEX installment_modifications_contract ON installment_modifications(contract_id);

-- The signed revised cash-flow schedule is stored separately from immutable installment_rows.
CREATE TABLE installment_modified_cashflows (
  modification_id INTEGER NOT NULL REFERENCES installment_modifications(id),
  row_no          INTEGER NOT NULL CHECK (row_no > 0),
  due_date        TEXT NOT NULL CHECK (
    due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  amount          INTEGER NOT NULL CHECK (amount > 0),
  PRIMARY KEY (modification_id, row_no),
  UNIQUE (modification_id, due_date)
);

CREATE TRIGGER installment_modifications_no_update BEFORE UPDATE ON installment_modifications
  BEGIN SELECT RAISE(ABORT, 'تعديل تقسيط مرحّل: لا تعديل'); END;
CREATE TRIGGER installment_modifications_no_delete BEFORE DELETE ON installment_modifications
  BEGIN SELECT RAISE(ABORT, 'تعديل تقسيط مرحّل: لا حذف'); END;
CREATE TRIGGER installment_modified_cashflows_no_update BEFORE UPDATE ON installment_modified_cashflows
  BEGIN SELECT RAISE(ABORT, 'جدول معدل مرحّل: لا تعديل'); END;
CREATE TRIGGER installment_modified_cashflows_no_delete BEFORE DELETE ON installment_modified_cashflows
  BEGIN SELECT RAISE(ABORT, 'جدول معدل مرحّل: لا حذف'); END;
