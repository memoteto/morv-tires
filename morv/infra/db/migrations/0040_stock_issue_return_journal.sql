-- P04-4D.3.1: a supplier return has one journal and one stock issue per source line.
-- Keep every original column, primary key, FK, source uniqueness and immutable trigger.
-- Migrator backs up existing DBs, checks child FKs before commit, and restores FK enforcement.
CREATE TABLE stock_issues_v40 (
  id          INTEGER PRIMARY KEY,
  item_id     INTEGER NOT NULL REFERENCES items(id),
  qty         INTEGER NOT NULL CHECK (qty > 0),
  cost_total  INTEGER NOT NULL CHECK (cost_total > 0),
  source_type TEXT NOT NULL CHECK (length(source_type) > 0),
  source_id   INTEGER NOT NULL,
  journal_entry_id INTEGER NOT NULL REFERENCES journal_entries(id),
  user_id     INTEGER REFERENCES users(id),
  at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  source_line_id INTEGER NOT NULL DEFAULT 0
);
INSERT INTO stock_issues_v40 (id, item_id, qty, cost_total, source_type, source_id,
                             journal_entry_id, user_id, at, source_line_id)
  SELECT id, item_id, qty, cost_total, source_type, source_id,
         journal_entry_id, user_id, at, source_line_id FROM stock_issues;
DROP TABLE stock_issues;
ALTER TABLE stock_issues_v40 RENAME TO stock_issues;

CREATE UNIQUE INDEX stock_issues_source_unique ON stock_issues(source_type, source_id, source_line_id);
CREATE UNIQUE INDEX stock_issues_journal_unique_non_return ON stock_issues(journal_entry_id)
  WHERE source_type <> 'purchase_return';
CREATE INDEX stock_issues_journal_lookup ON stock_issues(journal_entry_id);
CREATE TRIGGER stock_issues_no_update BEFORE UPDATE ON stock_issues
BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE TRIGGER stock_issues_no_delete BEFORE DELETE ON stock_issues
BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: الحذف ممنوع'); END;

-- A return issue must reference its own real, non-reversal document and journal.
-- Also prevent cross-source sharing in either insertion order, including legacy rows.
CREATE TRIGGER stock_issues_return_journal_guard BEFORE INSERT ON stock_issues
WHEN (NEW.source_type = 'purchase_return' AND NOT EXISTS (
    SELECT 1 FROM purchase_returns r WHERE r.id=NEW.source_id
      AND r.kind='return' AND r.journal_entry_id=NEW.journal_entry_id
  )) OR EXISTS (
    SELECT 1 FROM stock_issues s WHERE s.journal_entry_id=NEW.journal_entry_id
      AND (NEW.source_type <> 'purchase_return' OR s.source_type <> 'purchase_return'
           OR s.source_id <> NEW.source_id)
  )
BEGIN SELECT RAISE(ABORT, 'قيد الصرف لا يشارك إلا بين سطور مرتجع المورد نفسه'); END;
