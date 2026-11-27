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


# Feature IDs use the owning domain, not the page on which someone wants a shortcut.
DOMAIN_OWNER = {
    "home": "home", "pos": "pos", "sale": "sales", "purchase": "purchases",
    "inventory": "inventory", "customer": "customers", "supplier": "suppliers",
    "installment": "installments", "treasury": "treasury", "report": "reports",
    "settings": "settings",
}

SLOTS = (
    "page_actions", "record_actions", "invoice_fields", "invoice_footer",
    "dialog_fields", "dialog_footer", "report_filters", "report_export", "settings_fields",
)

# Placement decisions for the current costing phases; no feature is implemented by this data.
FEATURE_SLOT = {
    "purchase.acquisition_cost.create": "record_actions",
    "purchase.acquisition_cost.history": "record_actions",
    "purchase.return.create": "record_actions",
    "sale.return.create": "record_actions",
    "purchase.tax.apply": "invoice_fields",
    "pos.tax.apply": "invoice_fields",
    "settings.tax.configure": "settings_fields",
    "settings.inventory_valuation.configure": "settings_fields",
    "inventory.valuation.create": "page_actions",
    "inventory.valuation.create_for_item": "record_actions",
    "inventory.valuation.history": "page_actions",
    "inventory.valuation.reverse": "record_actions",
    "inventory.reconcile": "page_actions",
    "report.inventory_aging.view": "report_filters",
    "report.inventory_valuation.view": "report_filters",
    "treasury.acquisition_cost.pay": "page_actions",
}

# ListPage subclasses, including the actual owners inside stacked and tabbed sections.
LIST_HOSTS = {
    ("purchases_page", "PurchasesListPage"): "purchases",
    ("sales_page", "SalesPage"): "sales",
    ("inventory_page", "ItemsPage"): "inventory",
    ("stock_pages", "LotsPage"): "inventory",
    ("stock_pages", "StockPage"): "inventory",
    ("customers_page", "CustomersPage"): "customers",
    ("suppliers_page", "SuppliersPage"): "suppliers",
    ("installments_page", "ContractsPage"): "installments",
    ("treasury_page", "TreasuryPage"): "treasury",
    ("reports_page", "ReportsPage"): "reports",
}

FEATURE_HOST = {
    "purchase.acquisition_cost.create": ("purchases_page", "PurchasesListPage"),
    "purchase.acquisition_cost.history": ("purchases_page", "PurchasesListPage"),
    "inventory.valuation.create": ("stock_pages", "StockPage"),
    "inventory.valuation.create_for_item": ("stock_pages", "StockPage"),
    "inventory.valuation.history": ("stock_pages", "StockPage"),
}


def validate_list_action(feature: str, module: str, page: str, slot: str) -> None:
    domain, _, _ = feature.partition(".")
    owner = DOMAIN_OWNER.get(domain)
    host = (module, page)
    if (owner is None or LIST_HOSTS.get(host) != owner
            or slot not in ("page_actions", "record_actions") or FEATURE_SLOT.get(feature) != slot
            or FEATURE_HOST.get(feature, host) != host):
        raise ValueError(f"Unapproved action placement: {feature} at {module}.{page}/{slot}")
