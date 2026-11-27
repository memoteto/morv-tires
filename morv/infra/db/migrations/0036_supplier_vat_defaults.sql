-- Explicit supplier VAT defaults, not evidence of input-tax deductibility.
-- Existing suppliers remain unknown; never infer VAT registration from tax_id.
ALTER TABLE suppliers ADD COLUMN vat_status TEXT NOT NULL DEFAULT 'unknown'
  CHECK (vat_status IN ('unknown', 'unregistered', 'registered'));
ALTER TABLE suppliers ADD COLUMN vat_prices_inclusive INTEGER NOT NULL DEFAULT 0
  CHECK (vat_prices_inclusive IN (0, 1));
