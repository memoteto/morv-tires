"""Frozen map of which section owns each feature; CI pins it to the shell navigation."""

# The application's sections, in navigation order. A new feature belongs to the section whose
# page already owns its documents and is built inside that page or a modal it opens, never as a
# new top-level entry. Adding or removing a section is a structural change the owner must approve,
# so CI pins this tuple to the shell's NAV (tests/test_ui_placement.py).
SECTIONS = (
    "home", "pos", "sales", "purchases", "inventory",
    "customers", "suppliers", "installments", "treasury", "reports", "settings",
)

# Each section's owning page class. CI pins this to the shell's page builders, so moving a
# section to a different page, or renaming its owner, also needs the owner's approval.
SECTION_OWNER = {
    "home": "DashboardPage",
    "pos": "PosPage",
    "sales": "SalesPage",
    "purchases": "PurchasesPage",
    "inventory": "InventoryPage",
    "customers": "CustomersPage",
    "suppliers": "SuppliersPage",
    "installments": "InstallmentsPage",
    "treasury": "TreasuryPage",
    "reports": "ReportsPage",
    "settings": "SettingsPage",
}
