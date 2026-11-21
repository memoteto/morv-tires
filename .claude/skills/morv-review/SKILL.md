---
name: morv-review
description: Use before declaring any Morv Tires change ready to merge, to review the diff as an independent reviewer and report confirmed defects and potential risks (security, accounting, logic, stock, data integrity). Also use when asked for an inspection or audit.
---

# Independent review before merge

You are a reviewer, not the author. Read the real diff (`git diff origin/main...HEAD`), not your memory of it.

## Steps
1. Check money: integer piasters, no float or REAL, rounding stated, debit equals credit, correct accounts, reversal mirrors the original.
2. Check integrity: writes inside `transaction`, replay by `request_key`, immutability triggers, no edit or delete of a posted document, period lock and till closing respected, ledger tie (report totals match the account).
3. Check permissions and refusal paths: who can do it, what is refused, what error the user sees (Arabic).
4. Check stock and cost effects if any (FIFO layers, returns, `stock_issues`).
5. Cross-feature scenario: if the change opens a new way for a balance to move (receipt, reversal, refund), run one scenario through the existing features (returns, reversals, limits) and check the balance sign and the report tie. Do it in a script with fake data.
6. Check layers (`core` without Qt/SQL, `ui` without SQL), docstring and comment rules, tests present for every rule and for the refusal cases.
7. Note defects you notice outside the task. Do not fix them without the owner's approval unless the fix is needed to finish the task safely.
8. Mark each finding with its class:
   - **Confirmed**: you reproduced it or can point to the exact code and a failing case.
   - **Potential risk**: plausible but unproven; say what evidence is missing.
   Never call a potential risk a confirmed defect. Do not invent issues to look critical.
9. For each finding give severity (critical, important, minor), effect, and affected file with line.

## Report rules
- Say "not ready to merge" if any critical issue, or any accounting, stock or data-integrity problem, is unresolved.
- Short is fine, but never drop a fact that affects the merge decision.
- State the limits of the check: tests passing and CI success do not prove absence of security flaws, and this is not a full security audit. UI was not seen visually if PySide6 is missing.
- In an inspection-only task change no file, plan or document.

## Good example 1
"Confirmed, important: `customer_receipts.post_receipt` accepts a date before the customer's first invoice (`morv/infra/customer_receipts.py:78`); failing case in test X. Fixed before merge."

## Good example 2
"Potential risk, minor: credit days are read from the customer's current card, so editing it re-ages old invoices. Not proven harmful; recorded as a stated limit."

## Bad example
"Looks good, all tests pass, safe to merge." Why it is bad: it names no check performed, treats green tests as proof of safety, and hides what was not covered.
