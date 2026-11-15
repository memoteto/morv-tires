-- الترقية 0012: تحصيل الأقساط. سند قبض مرحّل لا يُعدَّل ولا يُحذف؛ التصحيح بسند عكسي لاحقًا.
-- التحصيل يذهب لأقدم قسط، ولا يتجاوز المتبقي على العقد (morv/infra/installment_collections.py).

CREATE TABLE installment_collections (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  contract_id      INTEGER NOT NULL REFERENCES installment_contracts(id),
  collect_date     TEXT NOT NULL CHECK (collect_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  method           TEXT NOT NULL CHECK (method IN ('cash', 'card', 'transfer', 'wallet')),
  reference        TEXT,
  notes            TEXT,
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);
CREATE INDEX installment_collections_contract ON installment_collections(contract_id);

CREATE TABLE installment_allocations (
  id            INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES installment_collections(id),
  row_id        INTEGER NOT NULL REFERENCES installment_rows(id),
  amount        INTEGER NOT NULL CHECK (amount > 0)
);
CREATE INDEX installment_allocations_row ON installment_allocations(row_id);

CREATE TRIGGER installment_collections_no_update BEFORE UPDATE ON installment_collections BEGIN SELECT RAISE(ABORT, 'سند محصَّل: لا تعديل'); END;
CREATE TRIGGER installment_collections_no_delete BEFORE DELETE ON installment_collections BEGIN SELECT RAISE(ABORT, 'سند محصَّل: لا حذف'); END;
CREATE TRIGGER installment_allocations_no_update BEFORE UPDATE ON installment_allocations BEGIN SELECT RAISE(ABORT, 'توزيع محصَّل: لا تعديل'); END;
CREATE TRIGGER installment_allocations_no_delete BEFORE DELETE ON installment_allocations BEGIN SELECT RAISE(ABORT, 'توزيع محصَّل: لا حذف'); END;

INSERT OR IGNORE INTO user_permissions (user_id, perm, allowed)
  SELECT id, 'installments.collect', 1 FROM users WHERE role = 'manager';
