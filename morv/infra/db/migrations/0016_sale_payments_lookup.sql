-- 0016: speed up correlated payment lookups for instalment invoices and sale details.
-- Index only: do not rewrite posted invoices, payments, journal entries or stock records.
CREATE INDEX sale_payments_sale_method ON sale_payments(sale_id, method);
