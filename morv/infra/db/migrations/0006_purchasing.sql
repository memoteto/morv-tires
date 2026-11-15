-- الترقية 0006: القيود المحاسبية، فاتورة الشراء، الدفعات الفعلية (DOT)، طبقات تكلفة FIFO. (المرحلة 3)
-- الدفعات الفعلية (stock_lots) منفصلة عن طبقات التكلفة (cost_layers): الأولى ما على الرف،
-- والثانية قيمة المخزون الدفترية. يطابقهما reconcile() في الكميات، والطبقات والقيود في القيمة.
-- كل المبالغ INTEGER بالقرش. الجداول الدفترية لا تُعدَّل ولا تُحذف (تصحيحها بحركة عكسية).

CREATE TABLE journal_entries (
  id          INTEGER PRIMARY KEY,
  entry_date  TEXT NOT NULL CHECK (entry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  source_type TEXT NOT NULL CHECK (length(source_type) > 0),
  source_id   INTEGER NOT NULL,
  memo        TEXT,
  user_id     INTEGER REFERENCES users(id),
  posted_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
CREATE INDEX journal_entries_source ON journal_entries(source_type, source_id);

CREATE TABLE journal_lines (
  id           INTEGER PRIMARY KEY,
  entry_id     INTEGER NOT NULL REFERENCES journal_entries(id),
  line_no      INTEGER NOT NULL CHECK (line_no > 0),
  account_code TEXT NOT NULL REFERENCES accounts(code),
  debit        INTEGER NOT NULL DEFAULT 0 CHECK (debit >= 0),
  credit       INTEGER NOT NULL DEFAULT 0 CHECK (credit >= 0),
  CHECK ((debit > 0) <> (credit > 0)),
  UNIQUE (entry_id, line_no)
);
CREATE INDEX journal_lines_account ON journal_lines(account_code);

CREATE TRIGGER journal_lines_postable BEFORE INSERT ON journal_lines
WHEN (SELECT postable * active FROM accounts WHERE code = NEW.account_code) IS NOT 1
BEGIN SELECT RAISE(ABORT, 'الحساب غير قابل للترحيل'); END;

CREATE TABLE purchase_invoices (
  id                  INTEGER PRIMARY KEY,
  number              TEXT NOT NULL UNIQUE,
  request_key         TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),   -- يمنع تكرار الترحيل عند إعادة المحاولة
  payload_hash        TEXT NOT NULL,
  supplier_id         INTEGER NOT NULL REFERENCES suppliers(id),
  supplier_invoice_no TEXT NOT NULL COLLATE NOCASE CHECK (length(trim(supplier_invoice_no)) > 0),
  invoice_date        TEXT NOT NULL CHECK (invoice_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  received_date       TEXT NOT NULL CHECK (received_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  total               INTEGER NOT NULL CHECK (total > 0),
  journal_entry_id    INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  notes               TEXT,
  user_id             INTEGER REFERENCES users(id),
  posted_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  UNIQUE (supplier_id, supplier_invoice_no)
);

CREATE TABLE purchase_lines (
  id         INTEGER PRIMARY KEY,
  invoice_id INTEGER NOT NULL REFERENCES purchase_invoices(id),
  line_no    INTEGER NOT NULL CHECK (line_no > 0),
  item_id    INTEGER NOT NULL REFERENCES items(id),
  dot        TEXT CHECK (dot IS NULL OR dot GLOB '[0-9][0-9][0-9][0-9]'),
  qty        INTEGER NOT NULL CHECK (qty > 0),
  unit_cost  INTEGER NOT NULL CHECK (unit_cost > 0),
  line_total INTEGER NOT NULL CHECK (line_total = qty * unit_cost),
  UNIQUE (invoice_id, line_no)
);
CREATE INDEX purchase_lines_item ON purchase_lines(item_id);

-- الدفعة الفعلية: ما استُلم برقم مرجعي وDOT. purchase_unit_cost تكلفة الشراء التاريخية
-- وليست قيمة دفترية متبقية؛ القيمة الدفترية تُقرأ من cost_layers فقط.
CREATE TABLE stock_lots (
  id                 INTEGER PRIMARY KEY,
  ref_no             TEXT NOT NULL UNIQUE,
  item_id            INTEGER NOT NULL REFERENCES items(id),
  purchase_line_id   INTEGER NOT NULL UNIQUE REFERENCES purchase_lines(id),
  dot                TEXT CHECK (dot IS NULL OR dot GLOB '[0-9][0-9][0-9][0-9]'),
  mfg_key            INTEGER,
  received_date      TEXT NOT NULL,
  qty_received       INTEGER NOT NULL CHECK (qty_received > 0),
  qty_on_hand        INTEGER NOT NULL CHECK (qty_on_hand >= 0 AND qty_on_hand <= qty_received),
  purchase_unit_cost INTEGER NOT NULL CHECK (purchase_unit_cost > 0),
  CHECK ((dot IS NULL) = (mfg_key IS NULL))
);
CREATE INDEX stock_lots_item ON stock_lots(item_id, mfg_key, id);

-- طبقة التكلفة المحاسبية (FIFO): مجموعة التكلفة = الصنف نفسه، فلا تختلط أصناف مختلفة.
CREATE TABLE cost_layers (
  id               INTEGER PRIMARY KEY,
  item_id          INTEGER NOT NULL REFERENCES items(id),
  purchase_line_id INTEGER NOT NULL UNIQUE REFERENCES purchase_lines(id),
  received_date    TEXT NOT NULL,
  qty_received     INTEGER NOT NULL CHECK (qty_received > 0),
  cost_total       INTEGER NOT NULL CHECK (cost_total > 0),
  qty_remaining    INTEGER NOT NULL CHECK (qty_remaining >= 0 AND qty_remaining <= qty_received),
  cost_remaining   INTEGER NOT NULL CHECK (cost_remaining >= 0 AND cost_remaining <= cost_total),
  CHECK ((qty_remaining = 0) = (cost_remaining = 0))
);
CREATE INDEX cost_layers_open ON cost_layers(item_id, id) WHERE qty_remaining > 0;

-- عملية صرف: الرأس الذي يجمع الحركات الفعلية وتخصيصات التكلفة، ومنه تُتتبع وتُعكس لاحقًا.
CREATE TABLE stock_issues (
  id          INTEGER PRIMARY KEY,
  item_id     INTEGER NOT NULL REFERENCES items(id),
  qty         INTEGER NOT NULL CHECK (qty > 0),
  cost_total  INTEGER NOT NULL CHECK (cost_total > 0),
  source_type TEXT NOT NULL CHECK (length(source_type) > 0),
  source_id   INTEGER NOT NULL,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id     INTEGER REFERENCES users(id),
  at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);

-- الحركة الفعلية على دفعة: استلام (+) أو صرف (-).
CREATE TABLE stock_movements (
  id               INTEGER PRIMARY KEY,
  item_id          INTEGER NOT NULL REFERENCES items(id),
  lot_id           INTEGER NOT NULL REFERENCES stock_lots(id),
  kind             TEXT NOT NULL CHECK (kind IN ('receipt', 'issue')),
  qty              INTEGER NOT NULL CHECK (qty <> 0),
  purchase_line_id INTEGER REFERENCES purchase_lines(id),
  issue_id         INTEGER REFERENCES stock_issues(id),
  picked_non_oldest INTEGER NOT NULL DEFAULT 0 CHECK (picked_non_oldest IN (0, 1)),
  override_reason  TEXT,
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK ((kind = 'receipt') = (qty > 0)),
  CHECK ((kind = 'receipt') = (purchase_line_id IS NOT NULL AND issue_id IS NULL)),
  CHECK (picked_non_oldest = 0 OR length(trim(coalesce(override_reason, ''))) >= 3)
);
CREATE INDEX stock_movements_lot ON stock_movements(lot_id);
CREATE INDEX stock_movements_issue ON stock_movements(issue_id);

-- تخصيص التكلفة المحاسبي: أي طبقة استُهلكت وبكم، لكل عملية صرف. الكمية الموجبة استهلاك.
CREATE TABLE cost_allocations (
  id       INTEGER PRIMARY KEY,
  issue_id INTEGER NOT NULL REFERENCES stock_issues(id),
  layer_id INTEGER NOT NULL REFERENCES cost_layers(id),
  qty      INTEGER NOT NULL CHECK (qty <> 0),
  cost     INTEGER NOT NULL CHECK (cost >= 0)
);
CREATE INDEX cost_allocations_issue ON cost_allocations(issue_id);
CREATE INDEX cost_allocations_layer ON cost_allocations(layer_id);

-- حماية: لا تعديل ولا حذف في الجداول الدفترية.
CREATE TRIGGER journal_entries_no_update BEFORE UPDATE ON journal_entries BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER journal_entries_no_delete BEFORE DELETE ON journal_entries BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER journal_lines_no_update BEFORE UPDATE ON journal_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER journal_lines_no_delete BEFORE DELETE ON journal_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER purchase_invoices_no_update BEFORE UPDATE ON purchase_invoices BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER purchase_invoices_no_delete BEFORE DELETE ON purchase_invoices BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER purchase_lines_no_update BEFORE UPDATE ON purchase_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER purchase_lines_no_delete BEFORE DELETE ON purchase_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER stock_issues_no_update BEFORE UPDATE ON stock_issues BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER stock_issues_no_delete BEFORE DELETE ON stock_issues BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;
CREATE TRIGGER stock_movements_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: التعديل ممنوع'); END;
CREATE TRIGGER stock_movements_no_delete BEFORE DELETE ON stock_movements BEGIN SELECT RAISE(ABORT, 'حركة مرحّلة: الحذف ممنوع'); END;
CREATE TRIGGER cost_allocations_no_update BEFORE UPDATE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER cost_allocations_no_delete BEFORE DELETE ON cost_allocations BEGIN SELECT RAISE(ABORT, 'تخصيص مرحّل: الحذف ممنوع'); END;

-- الدفعات والطبقات: الرصيد وحده يتغير؛ بقية الأعمدة ثابتة، والحذف ممنوع.
CREATE TRIGGER stock_lots_fixed BEFORE UPDATE OF ref_no, item_id, purchase_line_id, dot, mfg_key, received_date,
  qty_received, purchase_unit_cost ON stock_lots BEGIN SELECT RAISE(ABORT, 'دفعة مرحّلة: بياناتها ثابتة'); END;
CREATE TRIGGER stock_lots_no_delete BEFORE DELETE ON stock_lots BEGIN SELECT RAISE(ABORT, 'دفعة مرحّلة: الحذف ممنوع'); END;
CREATE TRIGGER cost_layers_fixed BEFORE UPDATE OF item_id, purchase_line_id, received_date, qty_received, cost_total
  ON cost_layers BEGIN SELECT RAISE(ABORT, 'طبقة تكلفة: بياناتها ثابتة'); END;
CREATE TRIGGER cost_layers_no_delete BEFORE DELETE ON cost_layers BEGIN SELECT RAISE(ABORT, 'طبقة تكلفة: الحذف ممنوع'); END;
