-- Migration 0017: partners and manual treasury movements (transfer, expense, partner cash flows, supplier payment).
-- A sole owner is simply one partner: there is a single code path for both.
-- Every movement is a two-line ledger entry; reversal is a mirrored movement, never an edit or a delete.

CREATE TABLE partners (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL COLLATE NOCASE UNIQUE CHECK (length(trim(name)) > 0),
  current_account TEXT NOT NULL UNIQUE REFERENCES accounts(code),
  capital_account TEXT NOT NULL UNIQUE REFERENCES accounts(code),
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);

CREATE TRIGGER partners_no_delete BEFORE DELETE ON partners
BEGIN SELECT RAISE(ABORT, 'الشريك لا يُحذف؛ يُعطَّل فقط'); END;

CREATE TABLE treasury_movements (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  request_key      TEXT NOT NULL UNIQUE CHECK (length(request_key) >= 8),
  payload_hash     TEXT NOT NULL,
  kind             TEXT NOT NULL CHECK (kind IN
                     ('transfer', 'expense', 'partner_withdrawal', 'partner_deposit', 'partner_capital',
                      'supplier_payment', 'reversal')),
  movement_date    TEXT NOT NULL CHECK (movement_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  debit_account    TEXT NOT NULL REFERENCES accounts(code),
  credit_account   TEXT NOT NULL REFERENCES accounts(code),
  partner_id       INTEGER REFERENCES partners(id),
  supplier_id      INTEGER REFERENCES suppliers(id),
  reference        TEXT,
  memo             TEXT NOT NULL CHECK (length(trim(memo)) > 0),
  reverses_id      INTEGER UNIQUE REFERENCES treasury_movements(id),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')),
  CHECK (debit_account <> credit_account),
  CHECK ((kind = 'reversal') = (reverses_id IS NOT NULL)),
  CHECK (kind <> 'supplier_payment' OR supplier_id IS NOT NULL),
  CHECK (kind NOT IN ('partner_withdrawal', 'partner_deposit', 'partner_capital') OR partner_id IS NOT NULL)
);
CREATE INDEX treasury_movements_supplier ON treasury_movements(supplier_id) WHERE supplier_id IS NOT NULL;
CREATE INDEX treasury_movements_partner ON treasury_movements(partner_id) WHERE partner_id IS NOT NULL;

CREATE TRIGGER treasury_movements_no_update BEFORE UPDATE ON treasury_movements
BEGIN SELECT RAISE(ABORT, 'حركة خزينة مرحّلة: لا تعديل؛ التصحيح بحركة عكسية'); END;
CREATE TRIGGER treasury_movements_no_delete BEFORE DELETE ON treasury_movements
BEGIN SELECT RAISE(ABORT, 'حركة خزينة مرحّلة: لا حذف؛ التصحيح بحركة عكسية'); END;

-- Each partner gets a capital account (31nn) and a current account (33nn) with the same nn; the shared
-- 3100/3300 are retired unless something was already posted to them.
UPDATE accounts SET active = 0
 WHERE code IN ('3100', '3300') AND NOT EXISTS (SELECT 1 FROM journal_lines l WHERE l.account_code = accounts.code);
