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
