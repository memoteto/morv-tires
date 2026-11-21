-- يتطلب المُرحِّل تعطيل فحص العلاقات أثناءها (إعادة بناء جداول لها جداول ابنة)؛ ويتحقق قبل الإتمام.
-- الترقية 0031: المخزون الافتتاحي (دفعة وطبقة تكلفة بلا سطر شراء ولا تسوية). قرار 46.

DROP VIEW active_opening_lines;

CREATE TABLE opening_balance_lines_new (
  id               INTEGER PRIMARY KEY,
  opening_id       INTEGER NOT NULL REFERENCES opening_balances(id),
  line_no          INTEGER NOT NULL CHECK (line_no > 0),
  kind             TEXT NOT NULL CHECK (kind IN ('account', 'partner_capital', 'partner_current', 'retained',
                                                 'customer', 'supplier', 'inventory')),
  account          TEXT REFERENCES accounts(code),
  partner_id       INTEGER REFERENCES partners(id),
  customer_id      INTEGER REFERENCES customers(id),
  supplier_id      INTEGER REFERENCES suppliers(id),
  item_id          INTEGER REFERENCES items(id),
  qty              INTEGER,
  unit_cost        INTEGER,
  dot              TEXT CHECK (dot IS NULL OR dot GLOB '[0-9][0-9][0-9][0-9]'),
  amount           INTEGER NOT NULL CHECK (amount <> 0),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  UNIQUE (opening_id, line_no),
  CHECK ((kind = 'account') = (account IS NOT NULL)),
  CHECK ((kind IN ('partner_capital', 'partner_current')) = (partner_id IS NOT NULL)),
  CHECK ((kind = 'customer') = (customer_id IS NOT NULL)),
  CHECK ((kind = 'supplier') = (supplier_id IS NOT NULL)),
  CHECK ((kind = 'inventory') = (item_id IS NOT NULL)),
  CHECK (kind <> 'inventory' OR (qty > 0 AND unit_cost > 0 AND amount = qty * unit_cost)),
  CHECK (kind = 'inventory' OR (qty IS NULL AND unit_cost IS NULL AND dot IS NULL)),
  CHECK (kind NOT IN ('customer', 'supplier', 'partner_capital') OR amount > 0)
);
INSERT INTO opening_balance_lines_new (id, opening_id, line_no, kind, account, partner_id, customer_id, supplier_id,
                                       amount, journal_entry_id)
  SELECT id, opening_id, line_no, kind, account, partner_id, customer_id, supplier_id, amount, journal_entry_id
  FROM opening_balance_lines;
DROP TABLE opening_balance_lines;
ALTER TABLE opening_balance_lines_new RENAME TO opening_balance_lines;
CREATE INDEX opening_balance_lines_customer ON opening_balance_lines(customer_id);
CREATE INDEX opening_balance_lines_supplier ON opening_balance_lines(supplier_id);
CREATE VIEW active_opening_lines AS
SELECT l.*, o.opening_date FROM opening_balance_lines l JOIN opening_balances o ON o.id = l.opening_id
WHERE o.kind = 'opening' AND NOT EXISTS (SELECT 1 FROM opening_balances r WHERE r.reverses_id = o.id);
CREATE TRIGGER opening_balance_lines_no_update BEFORE UPDATE ON opening_balance_lines
BEGIN SELECT RAISE(ABORT, 'سطور رصيد افتتاحي مرحّل: لا تعديل'); END;
CREATE TRIGGER opening_balance_lines_no_delete BEFORE DELETE ON opening_balance_lines
BEGIN SELECT RAISE(ABORT, 'سطور رصيد افتتاحي مرحّل: لا حذف'); END;

CREATE TABLE stock_lots_new (
  id                 INTEGER PRIMARY KEY,
  ref_no             TEXT NOT NULL UNIQUE,
  item_id            INTEGER NOT NULL REFERENCES items(id),
  purchase_line_id   INTEGER UNIQUE REFERENCES purchase_lines(id),
  adjustment_line_id INTEGER UNIQUE REFERENCES stock_adjustment_lines(id),
  opening_line_id    INTEGER UNIQUE REFERENCES opening_balance_lines(id),
  dot                TEXT CHECK (dot IS NULL OR dot GLOB '[0-9][0-9][0-9][0-9]'),
  mfg_key            INTEGER,
  received_date      TEXT NOT NULL,
  qty_received       INTEGER NOT NULL CHECK (qty_received > 0),
  qty_on_hand        INTEGER NOT NULL CHECK (qty_on_hand >= 0 AND qty_on_hand <= qty_received),
  purchase_unit_cost INTEGER NOT NULL CHECK (purchase_unit_cost > 0),
  CHECK ((dot IS NULL) = (mfg_key IS NULL)),
  CHECK ((purchase_line_id IS NOT NULL) + (adjustment_line_id IS NOT NULL) + (opening_line_id IS NOT NULL) = 1)
);
INSERT INTO stock_lots_new (id, ref_no, item_id, purchase_line_id, adjustment_line_id, dot, mfg_key, received_date,
                            qty_received, qty_on_hand, purchase_unit_cost)
  SELECT id, ref_no, item_id, purchase_line_id, adjustment_line_id, dot, mfg_key, received_date, qty_received,
         qty_on_hand, purchase_unit_cost FROM stock_lots;
DROP TABLE stock_lots;
ALTER TABLE stock_lots_new RENAME TO stock_lots;
CREATE INDEX stock_lots_item ON stock_lots(item_id, mfg_key, id);
CREATE TRIGGER stock_lots_fixed BEFORE UPDATE OF ref_no, item_id, purchase_line_id, adjustment_line_id, opening_line_id,
  dot, mfg_key, received_date, qty_received, purchase_unit_cost ON stock_lots
BEGIN SELECT RAISE(ABORT, 'دفعة مرحّلة: بياناتها ثابتة'); END;
CREATE TRIGGER stock_lots_no_delete BEFORE DELETE ON stock_lots BEGIN SELECT RAISE(ABORT, 'دفعة مرحّلة: الحذف ممنوع'); END;
CREATE TRIGGER stock_lots_item_matches_line BEFORE INSERT ON stock_lots
WHEN NEW.purchase_line_id IS NOT NULL
 AND NEW.item_id IS NOT (SELECT item_id FROM purchase_lines WHERE id = NEW.purchase_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الدفعة لا تطابق سطر الشراء'); END;
CREATE TRIGGER stock_lots_item_matches_adjustment BEFORE INSERT ON stock_lots
WHEN NEW.adjustment_line_id IS NOT NULL
 AND NEW.item_id IS NOT (SELECT item_id FROM stock_adjustment_lines WHERE id = NEW.adjustment_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الدفعة لا تطابق سطر التسوية'); END;
CREATE TRIGGER stock_lots_item_matches_opening BEFORE INSERT ON stock_lots
WHEN NEW.opening_line_id IS NOT NULL
 AND NEW.item_id IS NOT (SELECT item_id FROM opening_balance_lines WHERE id = NEW.opening_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الدفعة لا تطابق سطر الرصيد الافتتاحي'); END;

CREATE TABLE cost_layers_new (
  id                 INTEGER PRIMARY KEY,
  item_id            INTEGER NOT NULL REFERENCES items(id),
  purchase_line_id   INTEGER UNIQUE REFERENCES purchase_lines(id),
  adjustment_line_id INTEGER UNIQUE REFERENCES stock_adjustment_lines(id),
  opening_line_id    INTEGER UNIQUE REFERENCES opening_balance_lines(id),
  received_date      TEXT NOT NULL,
  qty_received       INTEGER NOT NULL CHECK (qty_received > 0),
  cost_total         INTEGER NOT NULL CHECK (cost_total > 0),
  qty_remaining      INTEGER NOT NULL CHECK (qty_remaining >= 0 AND qty_remaining <= qty_received),
  cost_remaining     INTEGER NOT NULL CHECK (cost_remaining >= 0 AND cost_remaining <= cost_total),
  CHECK ((qty_remaining = 0) = (cost_remaining = 0)),
  CHECK ((purchase_line_id IS NOT NULL) + (adjustment_line_id IS NOT NULL) + (opening_line_id IS NOT NULL) = 1)
);
INSERT INTO cost_layers_new (id, item_id, purchase_line_id, adjustment_line_id, received_date, qty_received,
                             cost_total, qty_remaining, cost_remaining)
  SELECT id, item_id, purchase_line_id, adjustment_line_id, received_date, qty_received, cost_total, qty_remaining,
         cost_remaining FROM cost_layers;
DROP TABLE cost_layers;
ALTER TABLE cost_layers_new RENAME TO cost_layers;
CREATE INDEX cost_layers_open ON cost_layers(item_id, id) WHERE qty_remaining > 0;
CREATE TRIGGER cost_layers_fixed BEFORE UPDATE OF item_id, purchase_line_id, adjustment_line_id, opening_line_id,
  received_date, qty_received, cost_total ON cost_layers BEGIN SELECT RAISE(ABORT, 'طبقة تكلفة: بياناتها ثابتة'); END;
CREATE TRIGGER cost_layers_no_delete BEFORE DELETE ON cost_layers BEGIN SELECT RAISE(ABORT, 'طبقة تكلفة: الحذف ممنوع'); END;
CREATE TRIGGER cost_layers_item_matches_line BEFORE INSERT ON cost_layers
WHEN NEW.purchase_line_id IS NOT NULL
 AND NEW.item_id IS NOT (SELECT item_id FROM purchase_lines WHERE id = NEW.purchase_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الطبقة لا تطابق سطر الشراء'); END;
CREATE TRIGGER cost_layers_item_matches_adjustment BEFORE INSERT ON cost_layers
WHEN NEW.adjustment_line_id IS NOT NULL
 AND NEW.item_id IS NOT (SELECT item_id FROM stock_adjustment_lines WHERE id = NEW.adjustment_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الطبقة لا تطابق سطر التسوية'); END;
CREATE TRIGGER cost_layers_item_matches_opening BEFORE INSERT ON cost_layers
WHEN NEW.opening_line_id IS NOT NULL
 AND NEW.item_id IS NOT (SELECT item_id FROM opening_balance_lines WHERE id = NEW.opening_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الطبقة لا تطابق سطر الرصيد الافتتاحي'); END;

CREATE TABLE stock_movements_new (
  id                      INTEGER PRIMARY KEY,
  item_id                 INTEGER NOT NULL REFERENCES items(id),
  lot_id                  INTEGER NOT NULL REFERENCES stock_lots(id),
  kind                    TEXT NOT NULL CHECK (kind IN ('receipt', 'issue', 'return', 'restock', 'adjust_in',
                                                        'adjust_out', 'opening_in', 'opening_out')),
  qty                     INTEGER NOT NULL CHECK (qty <> 0),
  purchase_line_id        INTEGER REFERENCES purchase_lines(id),
  issue_id                INTEGER REFERENCES stock_issues(id),
  return_line_id          INTEGER REFERENCES sale_return_lines(id),
  purchase_return_line_id INTEGER REFERENCES purchase_return_lines(id),
  adjustment_line_id      INTEGER REFERENCES stock_adjustment_lines(id),
  opening_line_id         INTEGER REFERENCES opening_balance_lines(id),
  picked_non_oldest       INTEGER NOT NULL DEFAULT 0 CHECK (picked_non_oldest IN (0, 1)),
  override_reason         TEXT,
  user_id                 INTEGER REFERENCES users(id),
  at                      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind IN ('issue', 'adjust_out', 'opening_out')) = (qty < 0)),
  CHECK ((kind = 'receipt') = (purchase_line_id IS NOT NULL)),
  CHECK ((kind = 'issue') = (issue_id IS NOT NULL)),
  CHECK ((kind = 'return') = (return_line_id IS NOT NULL)),
  CHECK ((kind = 'restock') = (purchase_return_line_id IS NOT NULL)),
  CHECK ((kind IN ('adjust_in', 'adjust_out')) = (adjustment_line_id IS NOT NULL)),
  CHECK ((kind IN ('opening_in', 'opening_out')) = (opening_line_id IS NOT NULL)),
  CHECK (picked_non_oldest = 0 OR length(trim(coalesce(override_reason, ''))) >= 3)
);
INSERT INTO stock_movements_new (id, item_id, lot_id, kind, qty, purchase_line_id, issue_id, return_line_id,
                                 purchase_return_line_id, adjustment_line_id, picked_non_oldest, override_reason,
                                 user_id, at)
  SELECT id, item_id, lot_id, kind, qty, purchase_line_id, issue_id, return_line_id, purchase_return_line_id,
         adjustment_line_id, picked_non_oldest, override_reason, user_id, at FROM stock_movements;
DROP TABLE stock_movements;
ALTER TABLE stock_movements_new RENAME TO stock_movements;
CREATE INDEX stock_movements_lot ON stock_movements(lot_id);
CREATE INDEX stock_movements_issue ON stock_movements(issue_id);
CREATE INDEX stock_movements_return ON stock_movements(return_line_id);
CREATE INDEX stock_movements_purchase_return ON stock_movements(purchase_return_line_id);
CREATE INDEX stock_movements_adjustment ON stock_movements(adjustment_line_id);
CREATE INDEX stock_movements_opening ON stock_movements(opening_line_id);
CREATE TRIGGER stock_movements_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: التعديل ممنوع'); END;
CREATE TRIGGER stock_movements_no_delete BEFORE DELETE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: الحذف ممنوع'); END;

CREATE TABLE cost_allocations_new (
  id                      INTEGER PRIMARY KEY,
  issue_id                INTEGER REFERENCES stock_issues(id),
  return_line_id          INTEGER REFERENCES sale_return_lines(id),
  purchase_return_line_id INTEGER REFERENCES purchase_return_lines(id),
  adjustment_line_id      INTEGER REFERENCES stock_adjustment_lines(id),
  opening_line_id         INTEGER REFERENCES opening_balance_lines(id),
  layer_id                INTEGER NOT NULL REFERENCES cost_layers(id),
  qty                     INTEGER NOT NULL CHECK (qty <> 0),
  cost                    INTEGER NOT NULL,
  CHECK ((issue_id IS NOT NULL) + (return_line_id IS NOT NULL) + (purchase_return_line_id IS NOT NULL)
         + (adjustment_line_id IS NOT NULL) + (opening_line_id IS NOT NULL) = 1),
  CHECK (CASE WHEN issue_id IS NOT NULL THEN qty > 0 AND cost >= 0
              WHEN adjustment_line_id IS NOT NULL OR opening_line_id IS NOT NULL
                THEN (qty > 0 AND cost >= 0) OR (qty < 0 AND cost <= 0)
              ELSE qty < 0 AND cost <= 0 END)
);
INSERT INTO cost_allocations_new (id, issue_id, return_line_id, purchase_return_line_id, adjustment_line_id, layer_id,
                                  qty, cost)
  SELECT id, issue_id, return_line_id, purchase_return_line_id, adjustment_line_id, layer_id, qty, cost
  FROM cost_allocations;
DROP TABLE cost_allocations;
ALTER TABLE cost_allocations_new RENAME TO cost_allocations;
CREATE INDEX cost_allocations_issue ON cost_allocations(issue_id);
CREATE INDEX cost_allocations_layer ON cost_allocations(layer_id);
CREATE INDEX cost_allocations_return ON cost_allocations(return_line_id);
CREATE INDEX cost_allocations_purchase_return ON cost_allocations(purchase_return_line_id);
CREATE INDEX cost_allocations_adjustment ON cost_allocations(adjustment_line_id);
CREATE INDEX cost_allocations_opening ON cost_allocations(opening_line_id);
CREATE TRIGGER cost_allocations_no_update BEFORE UPDATE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER cost_allocations_no_delete BEFORE DELETE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: الحذف ممنوع'); END;
