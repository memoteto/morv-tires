-- P04-4C.1: preserve gross payable semantics, add immutable VAT snapshots.
-- Rebuild only the two tables whose original CHECK equations exclude additive VAT.
-- The migrator disables FK enforcement for this transaction, checks all links
-- before commit, restores enforcement, and takes the existing pre-upgrade backup.
CREATE TABLE sales_v37 (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  customer_id      INTEGER REFERENCES customers(id),
  sale_date        TEXT NOT NULL CHECK (sale_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  subtotal         INTEGER NOT NULL CHECK (subtotal > 0),
  line_discount    INTEGER NOT NULL CHECK (line_discount >= 0),
  invoice_discount INTEGER NOT NULL CHECK (invoice_discount >= 0),
  total            INTEGER NOT NULL CHECK (total > 0),
  odometer         INTEGER CHECK (odometer IS NULL OR odometer >= 0),
  vehicle_plate    TEXT,
  notes            TEXT,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  tax_enabled      INTEGER NOT NULL DEFAULT 0 CHECK (tax_enabled IN (0, 1)),
  prices_tax_inclusive INTEGER NOT NULL DEFAULT 0 CHECK (prices_tax_inclusive IN (0, 1)),
  tax_total        INTEGER NOT NULL DEFAULT 0 CHECK (tax_total >= 0 AND tax_total <= total),
  CHECK (tax_enabled = 1 OR (prices_tax_inclusive = 0 AND tax_total = 0)),
  CHECK (total = subtotal - line_discount - invoice_discount
                 + CASE WHEN prices_tax_inclusive = 1 THEN 0 ELSE tax_total END)
);

CREATE TABLE sale_lines_v37 (
  id            INTEGER PRIMARY KEY,
  sale_id       INTEGER NOT NULL REFERENCES sales(id),
  line_no       INTEGER NOT NULL CHECK (line_no > 0),
  item_id       INTEGER NOT NULL REFERENCES items(id),
  qty           INTEGER NOT NULL CHECK (qty > 0),
  unit_price    INTEGER NOT NULL CHECK (unit_price > 0),
  line_discount INTEGER NOT NULL DEFAULT 0 CHECK (line_discount >= 0),
  invoice_share INTEGER NOT NULL DEFAULT 0 CHECK (invoice_share >= 0),
  net_total     INTEGER NOT NULL CHECK (net_total >= 0),
  tax_treatment TEXT NOT NULL DEFAULT 'none' CHECK (tax_treatment IN ('none', 'standard', 'exempt', 'zero')),
  tax_rate      INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate >= 0),
  tax_amount    INTEGER NOT NULL DEFAULT 0 CHECK (tax_amount >= 0 AND tax_amount <= net_total),
  prices_tax_inclusive INTEGER NOT NULL DEFAULT 0 CHECK (prices_tax_inclusive IN (0, 1)),
  UNIQUE (sale_id, line_no),
  CHECK (tax_treatment = 'standard' OR (tax_rate = 0 AND tax_amount = 0)),
  CHECK (tax_treatment <> 'none' OR prices_tax_inclusive = 0),
  CHECK (net_total = qty * unit_price - line_discount - invoice_share
                     + CASE WHEN prices_tax_inclusive = 1 THEN 0 ELSE tax_amount END)
);

INSERT INTO sales_v37 (id, number, request_key, payload_hash, customer_id, sale_date, subtotal, line_discount, invoice_discount, total, odometer, vehicle_plate, notes, journal_entry_id, user_id, at)
  SELECT id, number, request_key, payload_hash, customer_id, sale_date, subtotal, line_discount, invoice_discount, total, odometer, vehicle_plate, notes, journal_entry_id, user_id, at FROM sales;
INSERT INTO sale_lines_v37 (id, sale_id, line_no, item_id, qty, unit_price, line_discount, invoice_share, net_total)
  SELECT id, sale_id, line_no, item_id, qty, unit_price, line_discount, invoice_share, net_total FROM sale_lines;

DROP TABLE sale_lines;
DROP TABLE sales;
ALTER TABLE sales_v37 RENAME TO sales;
ALTER TABLE sale_lines_v37 RENAME TO sale_lines;

CREATE TRIGGER sales_no_update BEFORE UPDATE ON sales BEGIN SELECT RAISE(ABORT, 'فاتورة مرحّلة: لا تعديل، والتصحيح بمرتجع'); END;
CREATE TRIGGER sales_no_delete BEFORE DELETE ON sales BEGIN SELECT RAISE(ABORT, 'فاتورة مرحّلة: لا حذف، والتصحيح بمرتجع'); END;
CREATE TRIGGER sale_lines_no_update BEFORE UPDATE ON sale_lines BEGIN SELECT RAISE(ABORT, 'سطور فاتورة مرحّلة: لا تعديل'); END;
CREATE TRIGGER sale_lines_no_delete BEFORE DELETE ON sale_lines BEGIN SELECT RAISE(ABORT, 'سطور فاتورة مرحّلة: لا حذف'); END;
