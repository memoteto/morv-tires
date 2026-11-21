---
name: morv-feature
description: Use when asked to build a new financial or stock feature in Morv Tires that needs a database migration, an infra module, tests and a decision entry (voucher, document, ledger-posting function, persisted record). Not for reports (use morv-report) or bug fixes (use morv-bugfix).
---

# Build a financial feature in Morv Tires

One job: take one approved step from idea to a tested, committable change. Do only the requested step.

## Steps
1. Read only the files the step touches. Read one neighbouring module with the same shape (for example `installments.collect_payment` for a voucher, `safe_count.py` for a count document) and copy its pattern.
2. Check the rules in `CLAUDE.md` that apply: integer piasters only, no tables for inactive functions, no `BEGIN`/`COMMIT` by hand, layers (`core` no Qt/SQL, `ui` no SQL).
3. Migration: new numbered file in `morv/infra/db/migrations/` (next number after the highest). Never edit a merged migration. Documents get `request_key` UNIQUE (8+ chars), `payload_hash`, update and delete triggers that abort, and reversal as a second row with a UNIQUE `reverses_id`/`cancels_id`.
4. Infra module: every write pair inside `with transaction(con):`, ledger entry through `ledger.post_entry`, an `audit(...)` row, replay by `request_key` returns the same document, a changed payload under the same key is refused, permission check first.
5. Permission: add to `morv/core/security.py` only if no existing one fits, and say which roles get it.
6. UI (only if the step asks): `Modal` dialogs, `parse_money`, one `uuid` request key per dialog, no `QMessageBox`, no SQL.
7. Tests: local infra tests for every rule you added plus immutability, replay and refusal cases, and the permission check on every action (post and reverse separately). UI tests run in CI only (PySide6 is missing locally); keep page objects in variables and avoid blocking dialogs. Test dates must not be in the future.
8. Run `ruff check morv tests` then `python run_tests.py`. Fix failures at the root cause.
9. Add the next numbered entry to `docs/design_decisions.md` (Arabic, ends with a "حد صريح" line) only because it is part of an approved step. Do not touch the plan or docs in an inspection-only task.
10. Hand over to `morv-review`, then `morv-pr-merge`.

## Rules
- Docstring first line of every file is one line of 100 characters or fewer, in English. A function gets a docstring only for a business rule, rounding, refusal condition or non-obvious reason, also 100 characters or fewer. Count the characters.
- Comments explain why, never what, and contain no Arabic. User-facing messages are Arabic.
- Never use real customer data or open `tire_shop_live.db`.
- Say plainly what was not tested.

## Good example 1
A customer receipt: migration `0023_customer_receipts.sql` with triggers, `customer_receipts.py` with `post_receipt` and `reverse_receipt`, ledger entry debit payment account and credit 1310, 10 infra tests, decision item 32.

## Good example 2
A bank reconciliation record that posts no journal entry: the program balance is frozen into the row at save time and the save is refused if the balance changed since the dialog opened.

## Bad example
Adding a "settlement" table "in case it is needed later". Why it is bad: `CLAUDE.md` forbids tables for inactive functions; it is added by a migration when it is activated.
