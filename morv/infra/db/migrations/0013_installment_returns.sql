-- 0013: immutable installment-return settlements and transparent refund allocation.
-- The sale return remains the single reversing document; its source invoice, contract and paid rows remain immutable.
-- This migration rebuilds only sale_returns to extend its CHECK with installment.
-- Foreign keys are disabled by the migrator ONLY for this atomic rebuild, then checked before COMMIT.

CREATE TABLE sale_returns_rebuilt (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  sale_id          INTEGER NOT NULL REFERENCES sales(id),
  return_date      TEXT NOT NULL CHECK (return_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  total            INTEGER NOT NULL CHECK (total >= 0),
  cost_total       INTEGER NOT NULL CHECK (cost_total >= 0),
  reason           TEXT NOT NULL CHECK (length(trim(reason)) >= 3),
  refund_method    TEXT NOT NULL
                   CHECK (refund_method IN ('cash', 'card', 'transfer', 'wallet', 'credit', 'installment')),
  notes            TEXT,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
INSERT INTO sale_returns_rebuilt
  (id, number, request_key, payload_hash, sale_id, return_date, total, cost_total,
   reason, refund_method, notes, journal_entry_id, user_id, at)
  SELECT id, number, request_key, payload_hash, sale_id, return_date, total, cost_total,
         reason, refund_method, notes, journal_entry_id, user_id, at
  FROM sale_returns;
DROP TABLE sale_returns;
ALTER TABLE sale_returns_rebuilt RENAME TO sale_returns;
CREATE INDEX sale_returns_sale ON sale_returns(sale_id);
CREATE TRIGGER sale_returns_no_update BEFORE UPDATE ON sale_returns
  BEGIN SELECT RAISE(ABORT, 'مرتجع مرحّل: لا تعديل'); END;
CREATE TRIGGER sale_returns_no_delete BEFORE DELETE ON sale_returns
  BEGIN SELECT RAISE(ABORT, 'مرتجع مرحّل: لا حذف'); END;

-- Each installment return reverses sales revenue, unearned markup and either cash already paid
-- or the remaining installment receivable. Amounts are integer piasters.
CREATE TABLE installment_return_adjustments (
  return_id          INTEGER PRIMARY KEY REFERENCES sale_returns(id),
  contract_id        INTEGER NOT NULL REFERENCES installment_contracts(id),
  principal          INTEGER NOT NULL CHECK (principal >= 0),
  markup             INTEGER NOT NULL CHECK (markup >= 0),
  refunded           INTEGER NOT NULL CHECK (refunded >= 0),
  receivable_reduced INTEGER NOT NULL CHECK (receivable_reduced >= 0),
  CHECK (principal + markup = refunded + receivable_reduced)
);
CREATE INDEX installment_return_adjustments_contract
  ON installment_return_adjustments(contract_id);

-- Refunds are credited to the exact original receipt accounts, not assumed to be cash.
CREATE TABLE installment_return_refunds (
  return_id INTEGER NOT NULL REFERENCES installment_return_adjustments(return_id),
  method    TEXT NOT NULL CHECK (method IN ('cash', 'card', 'transfer', 'wallet')),
  amount    INTEGER NOT NULL CHECK (amount > 0),
  PRIMARY KEY (return_id, method)
);

-- Existing installment_rows and installment_payment_lines remain untouched.
-- A cancellation applies only to the unpaid part of a scheduled row.
CREATE TABLE installment_return_allocations (
  return_id INTEGER NOT NULL REFERENCES installment_return_adjustments(return_id),
  row_id    INTEGER NOT NULL REFERENCES installment_rows(id),
  amount    INTEGER NOT NULL CHECK (amount > 0),
  PRIMARY KEY (return_id, row_id)
);
CREATE INDEX installment_return_allocations_row ON installment_return_allocations(row_id);

CREATE TRIGGER installment_return_adjustments_no_update BEFORE UPDATE ON installment_return_adjustments
  BEGIN SELECT RAISE(ABORT, 'تسوية مرتجع تقسيط مرحّلة: لا تعديل'); END;
CREATE TRIGGER installment_return_adjustments_no_delete BEFORE DELETE ON installment_return_adjustments
  BEGIN SELECT RAISE(ABORT, 'تسوية مرتجع تقسيط مرحّلة: لا حذف'); END;
CREATE TRIGGER installment_return_refunds_no_update BEFORE UPDATE ON installment_return_refunds
  BEGIN SELECT RAISE(ABORT, 'رد نقدي مرحّل: لا تعديل'); END;
CREATE TRIGGER installment_return_refunds_no_delete BEFORE DELETE ON installment_return_refunds
  BEGIN SELECT RAISE(ABORT, 'رد نقدي مرحّل: لا حذف'); END;
CREATE TRIGGER installment_return_allocations_no_update BEFORE UPDATE ON installment_return_allocations
  BEGIN SELECT RAISE(ABORT, 'إلغاء قسط مرحّل: لا تعديل'); END;
CREATE TRIGGER installment_return_allocations_no_delete BEFORE DELETE ON installment_return_allocations
  BEGIN SELECT RAISE(ABORT, 'إلغاء قسط مرحّل: لا حذف'); END;
