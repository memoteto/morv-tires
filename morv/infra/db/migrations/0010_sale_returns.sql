-- الترقية 0010: مرتجع البيع. المرتجع مستند عكسي مستقل؛ الفاتورة الأصلية تبقى كما هي.
-- السليم يعود للمخزون بدفعته وطبقته الأصلية، والمعيب يذهب لحساب المخزون المعيب بلا رصيد بيع.

CREATE TABLE sale_returns (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  sale_id          INTEGER NOT NULL REFERENCES sales(id),
  return_date      TEXT NOT NULL CHECK (return_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  total            INTEGER NOT NULL CHECK (total >= 0),
  cost_total       INTEGER NOT NULL CHECK (cost_total >= 0),
  reason           TEXT NOT NULL CHECK (length(trim(reason)) >= 3),
  refund_method    TEXT NOT NULL CHECK (refund_method IN ('cash', 'card', 'transfer', 'wallet', 'credit')),
  notes            TEXT,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
CREATE INDEX sale_returns_sale ON sale_returns(sale_id);

CREATE TABLE sale_return_lines (
  id           INTEGER PRIMARY KEY,
  return_id    INTEGER NOT NULL REFERENCES sale_returns(id),
  line_no      INTEGER NOT NULL CHECK (line_no > 0),
  sale_line_id INTEGER NOT NULL REFERENCES sale_lines(id),
  qty          INTEGER NOT NULL CHECK (qty > 0),
  condition    TEXT NOT NULL CHECK (condition IN ('sound', 'defective')),
  value        INTEGER NOT NULL CHECK (value >= 0),
  cost         INTEGER NOT NULL CHECK (cost >= 0),
  UNIQUE (return_id, line_no)
);
CREATE INDEX sale_return_lines_sale_line ON sale_return_lines(sale_line_id);

CREATE TRIGGER sale_returns_no_update BEFORE UPDATE ON sale_returns BEGIN SELECT RAISE(ABORT, 'مرتجع مرحّل: لا تعديل'); END;
CREATE TRIGGER sale_returns_no_delete BEFORE DELETE ON sale_returns BEGIN SELECT RAISE(ABORT, 'مرتجع مرحّل: لا حذف'); END;
CREATE TRIGGER sale_return_lines_no_update BEFORE UPDATE ON sale_return_lines BEGIN SELECT RAISE(ABORT, 'سطور مرتجع مرحّل: لا تعديل'); END;
CREATE TRIGGER sale_return_lines_no_delete BEFORE DELETE ON sale_return_lines BEGIN SELECT RAISE(ABORT, 'سطور مرتجع مرحّل: لا حذف'); END;

-- إعادة بناء حركات المخزون لتقبل حركة «عودة» موجبة مرتبطة بسطر مرتجع.
ALTER TABLE stock_movements RENAME TO stock_movements_old;
CREATE TABLE stock_movements (
  id                INTEGER PRIMARY KEY,
  item_id           INTEGER NOT NULL REFERENCES items(id),
  lot_id            INTEGER NOT NULL REFERENCES stock_lots(id),
  kind              TEXT NOT NULL CHECK (kind IN ('receipt', 'issue', 'return')),
  qty               INTEGER NOT NULL CHECK (qty <> 0),
  purchase_line_id  INTEGER REFERENCES purchase_lines(id),
  issue_id          INTEGER REFERENCES stock_issues(id),
  return_line_id    INTEGER REFERENCES sale_return_lines(id),
  picked_non_oldest INTEGER NOT NULL DEFAULT 0 CHECK (picked_non_oldest IN (0, 1)),
  override_reason   TEXT,
  user_id           INTEGER REFERENCES users(id),
  at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'issue') = (qty < 0)),
  CHECK ((kind = 'receipt') = (purchase_line_id IS NOT NULL)),
  CHECK ((kind = 'issue') = (issue_id IS NOT NULL)),
  CHECK ((kind = 'return') = (return_line_id IS NOT NULL)),
  CHECK (picked_non_oldest = 0 OR length(trim(coalesce(override_reason, ''))) >= 3)
);
INSERT INTO stock_movements (id, item_id, lot_id, kind, qty, purchase_line_id, issue_id, picked_non_oldest,
                             override_reason, user_id, at)
  SELECT id, item_id, lot_id, kind, qty, purchase_line_id, issue_id, picked_non_oldest,
         override_reason, user_id, at FROM stock_movements_old;
DROP TABLE stock_movements_old;
CREATE INDEX stock_movements_lot ON stock_movements(lot_id);
CREATE INDEX stock_movements_issue ON stock_movements(issue_id);
CREATE INDEX stock_movements_return ON stock_movements(return_line_id);
CREATE TRIGGER stock_movements_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: التعديل ممنوع'); END;
CREATE TRIGGER stock_movements_no_delete BEFORE DELETE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: الحذف ممنوع'); END;

-- إعادة بناء تخصيصات التكلفة: تخصيص صرف موجب، أو ردّ طبقة سالب مرتبط بسطر مرتجع.
ALTER TABLE cost_allocations RENAME TO cost_allocations_old;
CREATE TABLE cost_allocations (
  id             INTEGER PRIMARY KEY,
  issue_id       INTEGER REFERENCES stock_issues(id),
  return_line_id INTEGER REFERENCES sale_return_lines(id),
  layer_id       INTEGER NOT NULL REFERENCES cost_layers(id),
  qty            INTEGER NOT NULL CHECK (qty <> 0),
  cost           INTEGER NOT NULL,
  CHECK ((issue_id IS NULL) <> (return_line_id IS NULL)),
  CHECK ((issue_id IS NOT NULL) = (qty > 0)),
  CHECK (CASE WHEN issue_id IS NOT NULL THEN cost >= 0 ELSE cost <= 0 END)
);
INSERT INTO cost_allocations (id, issue_id, layer_id, qty, cost)
  SELECT id, issue_id, layer_id, qty, cost FROM cost_allocations_old;
DROP TABLE cost_allocations_old;
CREATE INDEX cost_allocations_issue ON cost_allocations(issue_id);
CREATE INDEX cost_allocations_layer ON cost_allocations(layer_id);
CREATE INDEX cost_allocations_return ON cost_allocations(return_line_id);
CREATE TRIGGER cost_allocations_no_update BEFORE UPDATE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER cost_allocations_no_delete BEFORE DELETE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: الحذف ممنوع'); END;

-- صلاحية المرتجع للمديرين الموجودين قبل هذه الترقية (الجدد تأخذها من قالب الدور).
INSERT OR IGNORE INTO user_permissions (user_id, perm, allowed)
  SELECT id, 'sales.return', 1 FROM users WHERE role = 'manager';
