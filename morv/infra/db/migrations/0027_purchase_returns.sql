-- Migration 0027: returns of purchased stock to the supplier, with a mirrored reversal document.
-- Stock leaves through stock_issues (source 'purchase_return'); a reversal puts it back with a 'restock'
-- movement and a negative cost allocation, so lots, layers and the ledger stay reconcilable.

CREATE TABLE purchase_returns (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  supplier_id      INTEGER NOT NULL REFERENCES suppliers(id),
  kind             TEXT NOT NULL CHECK (kind IN ('return', 'reversal')),
  return_date      TEXT NOT NULL CHECK (return_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  total            INTEGER NOT NULL CHECK (total > 0),
  reason           TEXT NOT NULL CHECK (length(trim(reason)) >= 3),
  reverses_id      INTEGER UNIQUE REFERENCES purchase_returns(id),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL))
);

CREATE TABLE purchase_return_lines (
  id               INTEGER PRIMARY KEY,
  return_id        INTEGER NOT NULL REFERENCES purchase_returns(id),
  line_no          INTEGER NOT NULL CHECK (line_no > 0),
  purchase_line_id INTEGER NOT NULL REFERENCES purchase_lines(id),
  qty              INTEGER NOT NULL CHECK (qty > 0),
  value            INTEGER NOT NULL CHECK (value > 0),
  issue_id         INTEGER REFERENCES stock_issues(id),
  UNIQUE (return_id, line_no)
);
CREATE INDEX purchase_return_lines_line ON purchase_return_lines(purchase_line_id);

CREATE TRIGGER purchase_returns_no_update BEFORE UPDATE ON purchase_returns
BEGIN SELECT RAISE(ABORT, 'مرتجع مورد مرحّل: لا تعديل؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER purchase_returns_no_delete BEFORE DELETE ON purchase_returns
BEGIN SELECT RAISE(ABORT, 'مرتجع مورد مرحّل: لا حذف؛ التصحيح بمستند عكسي'); END;
CREATE TRIGGER purchase_return_lines_no_update BEFORE UPDATE ON purchase_return_lines
BEGIN SELECT RAISE(ABORT, 'سطور مرتجع مورد مرحّل: لا تعديل'); END;
CREATE TRIGGER purchase_return_lines_no_delete BEFORE DELETE ON purchase_return_lines
BEGIN SELECT RAISE(ABORT, 'سطور مرتجع مورد مرحّل: لا حذف'); END;

-- Stock movements accept a positive 'restock' movement tied to a reversal line.
ALTER TABLE stock_movements RENAME TO stock_movements_old;
CREATE TABLE stock_movements (
  id                      INTEGER PRIMARY KEY,
  item_id                 INTEGER NOT NULL REFERENCES items(id),
  lot_id                  INTEGER NOT NULL REFERENCES stock_lots(id),
  kind                    TEXT NOT NULL CHECK (kind IN ('receipt', 'issue', 'return', 'restock')),
  qty                     INTEGER NOT NULL CHECK (qty <> 0),
  purchase_line_id        INTEGER REFERENCES purchase_lines(id),
  issue_id                INTEGER REFERENCES stock_issues(id),
  return_line_id          INTEGER REFERENCES sale_return_lines(id),
  purchase_return_line_id INTEGER REFERENCES purchase_return_lines(id),
  picked_non_oldest       INTEGER NOT NULL DEFAULT 0 CHECK (picked_non_oldest IN (0, 1)),
  override_reason         TEXT,
  user_id                 INTEGER REFERENCES users(id),
  at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'issue') = (qty < 0)),
  CHECK ((kind = 'receipt') = (purchase_line_id IS NOT NULL)),
  CHECK ((kind = 'issue') = (issue_id IS NOT NULL)),
  CHECK ((kind = 'return') = (return_line_id IS NOT NULL)),
  CHECK ((kind = 'restock') = (purchase_return_line_id IS NOT NULL)),
  CHECK (picked_non_oldest = 0 OR length(trim(coalesce(override_reason, ''))) >= 3)
);
INSERT INTO stock_movements (id, item_id, lot_id, kind, qty, purchase_line_id, issue_id, return_line_id,
                             picked_non_oldest, override_reason, user_id, at)
  SELECT id, item_id, lot_id, kind, qty, purchase_line_id, issue_id, return_line_id,
         picked_non_oldest, override_reason, user_id, at FROM stock_movements_old;
DROP TABLE stock_movements_old;
CREATE INDEX stock_movements_lot ON stock_movements(lot_id);
CREATE INDEX stock_movements_issue ON stock_movements(issue_id);
CREATE INDEX stock_movements_return ON stock_movements(return_line_id);
CREATE INDEX stock_movements_purchase_return ON stock_movements(purchase_return_line_id);
CREATE TRIGGER stock_movements_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: التعديل ممنوع'); END;
CREATE TRIGGER stock_movements_no_delete BEFORE DELETE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: الحذف ممنوع'); END;

-- Cost allocations accept a negative allocation tied to a reversal line, restoring the layer.
ALTER TABLE cost_allocations RENAME TO cost_allocations_old;
CREATE TABLE cost_allocations (
  id                      INTEGER PRIMARY KEY,
  issue_id                INTEGER REFERENCES stock_issues(id),
  return_line_id          INTEGER REFERENCES sale_return_lines(id),
  purchase_return_line_id INTEGER REFERENCES purchase_return_lines(id),
  layer_id                INTEGER NOT NULL REFERENCES cost_layers(id),
  qty                     INTEGER NOT NULL CHECK (qty <> 0),
  cost                    INTEGER NOT NULL,
  CHECK ((issue_id IS NOT NULL) + (return_line_id IS NOT NULL) + (purchase_return_line_id IS NOT NULL) = 1),
  CHECK ((issue_id IS NOT NULL) = (qty > 0)),
  CHECK (CASE WHEN issue_id IS NOT NULL THEN cost >= 0 ELSE cost <= 0 END)
);
INSERT INTO cost_allocations (id, issue_id, return_line_id, layer_id, qty, cost)
  SELECT id, issue_id, return_line_id, layer_id, qty, cost FROM cost_allocations_old;
DROP TABLE cost_allocations_old;
CREATE INDEX cost_allocations_issue ON cost_allocations(issue_id);
CREATE INDEX cost_allocations_layer ON cost_allocations(layer_id);
CREATE INDEX cost_allocations_return ON cost_allocations(return_line_id);
CREATE INDEX cost_allocations_purchase_return ON cost_allocations(purchase_return_line_id);
CREATE TRIGGER cost_allocations_no_update BEFORE UPDATE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER cost_allocations_no_delete BEFORE DELETE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: الحذف ممنوع'); END;
