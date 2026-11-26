# POS improvement plan

Owner approval on 2026-11-25: retain the tyre, battery and service POS design direction. A separate grocery/supermarket interface is deferred; share business engines and UI primitives when needed, without speculative schema or features.

- POS-1: payment state safety, cancellation confirmation, full and partial credit using the existing engine.
- POS-2: item search, barcode and keyboard interaction.
- POS-3: simplify customer information.
- POS-4: payment summary and fixed approval controls.
- POS-5: invoice line layout.
- POS-6: performance, functional and visual verification.

## POS-1 diagnostic gate

Base main: a86b95fcb1356949e6e634949bcee8eca79ed80f. Code inspection confirms deferred credit choice, stale split values, stale payment method, cash-only data passed to other methods, missing received cash on second method, and cancellation without confirmation. Existing engine validates registered active customers, credit limits, deposits and balances; do not alter accounting rules. The instalment contract covers the entire invoice, so it should not be offered as a second method. This last condition is a proposed acceptance rule consistent with the existing primary instalment flow.

Added 14 tests in tests/test_ui_pos_payment_safety.py (commit 3d9982c4d59574165bc6c5aa28e68753f051895c). No application changes. Baseline execution pending on owner's isolated test checkout: distinguish regression failures from unimplemented credit cases, confirm rejection and ledger snapshots, keep existing tests intact. New tests exercise service invoices; stock/FIFO regression and real tyre-sale checks remain necessary after implementation. No production data, no migration, no merge.

## POS-1 implementation — 2026-11-25

Owner baseline on 4fbd8071c087197b738e8e2b3f4fc0a98a61a163: 14 tests, 13 expected failures, one pass, no errors (8.783 s). Hidden split actually posted card 6000 and cash 4000 instead of cash 10000; non-cash received was refused by the engine; missing credit UI accounts for five failures. Owner then authorized implementation and replacing the exact-amount chip with a click on the final total.

Implemented guarded cancellation, cash default after reset, clearing collapsed split, per-method cash/reference data, full and partial credit with existing limit/deposit/debt preview, no instalment as secondary method, and native clickable total using central QSS. For split payments the total fills only the cash share; non-cash and invalid splits do not fill. The rounded cash shortcuts remain. Shortened payment labels; no new payment-reference requirement or credit permission rule. Existing cancellation test now confirms the dialog while preserving its previous assertions. The original 14 diagnostic tests are unchanged; seven additional tests cover the exact total click, incomplete split, secondary reference and credit summary. Application changes only in POS and central theme. Post-fix CI, local tests, inventory regression and actual visual approval remain pending. No merge.

## POS-1 completion

Merged in PR #158 after successful local and CI tests and owner approval.

## POS-2 local approval

Shared invoice item search, unique barcode lookup and keyboard navigation.
All 97 selected local tests passed. Application code grew by 35 lines.
Owner approved local trial before upload. No schema or accounting changes.

POS-3 also covers supplier fields; POS-4 also covers the purchase summary.
POS-5: hide DOT for battery rows, preserve alignment and historical data,
and keep DOT mandatory for tyres.

## POS-2 completion and POS-3 local implementation

POS-2 merged in PR #159 after 1033 local tests, manual verification and successful CI.
POS-3: four baseline tests exposed six failures; simplified invoice headers and mobile lookup. 1061 local tests passed; Ruff and test discovery passed.
Owner design approval, 1061 tests on both platforms and CI passed; POS-3 merged in PR #160 (452e07d).
POS-4: four baseline tests failed for scrolling totals/actions; owner approved compact fixed summary, shared layout and deferred toast. Implementation verification pending.
POS-4 local: 1065 tests and Ruff passed; visual review identified gap, oversized controls and contrast defects. Seven added checks reproduced all seven defects. Owner approved refinement; repeat all eleven diagnostic tests and visual/CI verification.

POS-5 baseline: 10 tests, nine failing with 128 subtest failures; tyre DOT validation passed. Long names overflowed by 221/203 px and displaced the medium-width summary.
POS-5 implementation: shared invoice columns, wrapped names and battery DOT hidden; owner approved preserving alignment and name space. Local regression and visual verification pending.

POS search details: owner approved design 3 and removal of repeated specifications; Ubuntu baseline 1093 tests with 11 expected subtest failures.
Implemented sale tier price and batched physical stock with existing status pills; shared spec deduplication, no purchase sale prices. CI and visual review pending.
POS search CI: all 1093 tests passed on both platforms, all six jobs passed for db81f0f.
Added deterministic sale/purchase popup captures and geometry reporting at the existing three audit sizes and both themes; visual review pending.
Visual audit b054d69 confirmed all 12 captures, but clipped names at 1366/1093 and off-screen popups at 1093 in both themes.
Owner approved content-driven popup width, adaptive row height and screen-constrained scrolling with right alignment; new geometry/keyboard tests and CI pending.
CI geometry logs confirmed 2px native frame margins on both platforms; removing the native frame passed geometry tests but violated the UI architecture guard.
Restored tooltip flags and account for measured native frame margins in sizing and positioning; unchanged geometry assertions and all CI checks pending.
POS search completed: PR #164 merged at ebf5efc after 1095 tests per platform and 12 successful popup captures; owner explicitly approved merge, no device screenshot supplied.
POS-6A: added three mixed-stock UI integration cases for split discounts/DOT/FIFO, atomic short-stock retry and partial credit; CI verification pending, no application changes.
