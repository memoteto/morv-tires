---
name: morv-report
description: Use when asked to add or change a report in the Morv Tires reports catalog (morv/infra/reports.py): a read-only table built from posted ledger entries with CSV and PDF export.
---

# Add a report

## Steps
1. Read `morv/infra/reports.py` only around one similar report (for example `supplier_balances` for balances tied to an account, `installment_aging` for aging buckets).
2. Build a uniform `Table(key, title, subtitle, headers, kinds, rows, stretch)` with row kinds head/line/total/grand and column kinds TEXT/CODE/MONEY/DATE.
3. Register the key in `KEYS` and `TITLES`, add the branch in `build`, and in `NEEDS_PARAM` only if one choice is needed.
4. Money is integer piasters; never float. Dates in a subtitle are LTR-safe through the existing export path.
5. Tie the total to the matching ledger account as of the same end date and put "يطابق الدفتر" or a warning in the subtitle.
6. Reports read posted entries only, never write, and never need an annual close.
7. Tests in `tests/test_reports_batch*.py` style: values per bucket or row, as-of dates ignoring later documents, reversals, empty report, ledger tie. `tests/test_reports.py` already runs every key.
8. Run `ruff check`, `python run_tests.py`, then `morv-review` and `morv-pr-merge`. Add the next numbered entry to `docs/design_decisions.md` as part of the step.

## Rules
- Docstring of the new function: one line, 100 characters or fewer.
- State known limits in the decision entry (what the report cannot tell).
- Do not change the page UI unless the report needs a new parameter kind.

## Good example 1
Credit aging: per customer buckets, returns reduce their own invoice, receipts settle oldest first, subtitle compares the total with account 1310.

## Good example 2
Supplier balances: invoices minus payments up to the end date, tied to account 2110.

## Bad example
A report that sums a table directly and never compares with the ledger. Why it is bad: a mapping gap silently shows a number that disagrees with the books.
