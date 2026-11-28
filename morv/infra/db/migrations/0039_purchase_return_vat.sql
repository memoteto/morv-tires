-- P04-4D.3: additive immutable VAT snapshots; old returns remain exactly as posted.
-- Book value is not a VAT cap. Allocation reserves original VAT even when the
-- supplier's actual credit is smaller (e.g. freight not refunded).
ALTER TABLE purchase_returns ADD COLUMN tax_total INTEGER NOT NULL DEFAULT 0
  CHECK (tax_total >= 0 AND tax_total <= settlement_amount);
ALTER TABLE purchase_returns ADD COLUMN deductible_tax_total INTEGER NOT NULL DEFAULT 0
  CHECK (deductible_tax_total >= 0 AND deductible_tax_total <= tax_total);
ALTER TABLE purchase_returns ADD COLUMN credit_note_ref TEXT NOT NULL DEFAULT '';
ALTER TABLE purchase_returns ADD COLUMN settlement_reason TEXT NOT NULL DEFAULT '';

ALTER TABLE purchase_return_lines ADD COLUMN tax_allocated INTEGER NOT NULL DEFAULT 0
  CHECK (tax_allocated >= 0);
ALTER TABLE purchase_return_lines ADD COLUMN tax_amount INTEGER NOT NULL DEFAULT 0
  CHECK (tax_amount >= 0 AND tax_amount <= tax_allocated);
ALTER TABLE purchase_return_lines ADD COLUMN deductible_tax_amount INTEGER NOT NULL DEFAULT 0
  CHECK (deductible_tax_amount IN (0, tax_amount));
ALTER TABLE purchase_return_lines ADD COLUMN tax_mode TEXT NOT NULL DEFAULT 'legacy'
  CHECK (tax_mode IN ('legacy', 'auto', 'manual')
    AND (tax_mode <> 'legacy' OR (tax_allocated=0 AND tax_amount=0))
    AND (tax_mode <> 'auto' OR tax_amount=tax_allocated));
