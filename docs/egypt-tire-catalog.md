# Egyptian tyre catalogue

Approved scope (2026-11-25): a compact Egyptian-market seed, contains-search, manual entry of additional valid sizes, 19 selectable categories, and editable category suggestions. Exclude the three retired categories for new records but preserve their original values when editing existing items. No database migration.

Sources for the initial seed (market availability, not sales-volume rankings or fitment certification): https://tyresonline.eg/ ; https://www.kawetchagy.com/ ; https://marketplace.octane-tech.io/ar/category/tbr ; https://www.hanksugi.com/eg/tires/sizes/ ; https://eg.continental-me.com/ar/tires/sizes ; https://www.dunlop-mea.com/ar-eg/tyres/commercial/sp-van01 . The latter catalogue selection is illustrative, not exhaustive. Some source pages were available only as search excerpts.

The most frequent permitted saved category takes priority, ties use a matching seed hint or no suggestion. Retired categories are not learned. Manual override wins for the current dialog; editing an existing item never auto-changes its stored category. Only successfully saved item attributes extend the catalogue. No internet at runtime, no changes to accounting, stock, DOT, pricing or permissions.

Implemented on an isolated branch from 68a9096942c7e957c12bce09f36ee0803c9db93b. Added master-data and Qt regression/feature tests without weakening existing assertions. Local Qt execution is unavailable; CI, actual popup interactions and visual approval are outstanding. Do not merge before verification and owner approval.
