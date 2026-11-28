-- Store original-snapshot VAT reversed by each return; gross values and immutable history stay unchanged.
ALTER TABLE sale_returns ADD COLUMN tax_total INTEGER NOT NULL DEFAULT 0
  CHECK (tax_total >= 0 AND tax_total <= total);
ALTER TABLE sale_return_lines ADD COLUMN tax_amount INTEGER NOT NULL DEFAULT 0
  CHECK (tax_amount >= 0 AND tax_amount <= value);
