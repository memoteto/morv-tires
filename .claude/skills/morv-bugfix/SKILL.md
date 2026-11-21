---
name: morv-bugfix
description: Use when a Morv Tires test, CI job or screen behaves wrongly and needs a root-cause fix with a permanent regression test. Not for new features (use morv-feature).
---

# Diagnose, fix, test, repeat

## Steps
1. Reproduce first: run the failing test or read the CI job log. Quote the exact failure.
2. Find the root cause in the code, not by guessing. Separate what you verified from what you assume.
3. Fix inside the approved scope. Keep existing behaviour, approved design, accounting and stock rules.
4. Write or keep a permanent test that fails before the fix and passes after. Every discovered bug becomes a test.
5. Run `ruff check morv tests` and `python run_tests.py`; repeat until green.
6. Stop and ask the owner if the fix needs a scope change, a rule change, or migrating operational data.

## Rules
- Never disable a test, loosen an expectation, or add a workaround that hides the error.
- If the test expectation was wrong, say so and show why (for example a pound value where piasters were expected).
- A merged migration is never edited; fix forward with a new one.
- Common causes seen here: docstring first line over 100 characters; ledger posting outside a transaction; future test dates (`ledger.valid_date` refuses them); non-postable account in a test entry; a Qt page object garbage collected in a UI test.

## Good example 1
Test failed with "ترحيل القيد يجب أن يكون داخل معاملة": the test helper posted without `transaction`; wrapped the helper, no production change.

## Good example 2
UI test failed only in CI with `'أقل' not found in '—'`: the date was in the future, so the program balance was undefined; changed the test to use today and kept the assertion.

## Bad example
Wrapping the failing assertion in `skipTest` or widening the expected value. Why it is bad: the bug stays and the suite stops protecting that rule.
