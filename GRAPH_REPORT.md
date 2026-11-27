# Graph Report - morv  (2026-11-27)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 2656 nodes · 5673 edges · 152 communities (92 shown, 60 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 249 edges (avg confidence: 0.94)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `77db6bb4`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49
- Community 50
- Community 51
- Community 52
- Community 53
- Community 54
- Community 55
- Community 56
- Community 58
- Community 59
- Community 60
- Community 61
- Community 62
- Community 63
- Community 64
- Community 65
- Community 66
- Community 67
- Community 68
- Community 69
- Community 70
- Community 71
- Community 72
- Community 73
- Community 74
- Community 75
- Community 76
- Community 77
- Community 78
- Community 79
- Community 80
- Community 81
- Community 82
- Community 83
- Community 84
- Community 85
- Community 86
- Community 87
- Community 88
- Community 89
- Community 90
- Community 91
- Community 92
- Community 93
- Community 94
- Community 95
- Community 96
- Community 97
- Community 98
- Community 100
- Community 101
- Community 102
- Community 103
- Community 104
- Community 105
- Community 106
- Community 107
- Community 108
- Community 109
- Community 110
- Community 111
- Community 112
- Community 113
- Community 114
- Community 115
- Community 116
- Community 117
- Community 118
- Community 119
- Community 120
- Community 121
- Community 122
- Community 123
- Community 124
- Community 125
- Community 126
- Community 127
- Community 128
- Community 129
- Community 130
- Community 131
- Community 132
- Community 133
- Community 134
- Community 135
- Community 136
- Community 137

## God Nodes (most connected - your core abstractions)
1. `PosPage` - 61 edges
2. `Modal` - 58 edges
3. `ListPage` - 45 edges
4. `MainWindow` - 38 edges
5. `PurchaseForm` - 36 edges
6. `SetupWizard` - 32 edges
7. `users` - 32 edges
8. `journal_entries` - 31 edges
9. `DateEdit` - 28 edges
10. `ReportsPage` - 27 edges

## Surprising Connections (you probably didn't know these)
- `post_sale()` --uses--> `Payment`  [INFERRED]
  infra/sales.py → core/sales_rules.py
- `PosPage` --uses--> `MoneyError`  [INFERRED]
  ui/pos_page.py → core/money.py
- `PosPage` --uses--> `SaleError`  [INFERRED]
  ui/pos_page.py → infra/sales.py
- `ContractForm` --uses--> `MoneyError`  [INFERRED]
  ui/installments_page.py → core/money.py
- `ContractForm` --uses--> `ContractError`  [INFERRED]
  ui/installments_page.py → infra/installments.py

## Import Cycles
- None detected.

## Communities (152 total, 60 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (4): SaleLineIn, _ElidedSummary, FoldableDetails, PosPage

### Community 1 - "Community 1"
Cohesion: 0.07
Nodes (41): _band(), body_html(), _contact_bar(), content_px(), Document, document_html(), footer_html(), _gap() (+33 more)

### Community 2 - "Community 2"
Cohesion: 0.07
Nodes (34): account_choices(), account_statement(), _aligned(), _bp_text(), build(), _cell(), collections(), credit_aging() (+26 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (18): _allocate_freight(), _check_items(), compute_allocations(), last_cost(), _legacy_payload_hash(), LineAllocation, next_number_preview(), _payload_hash() (+10 more)

### Community 4 - "Community 4"
Cohesion: 0.05
Nodes (58): cost_layers, cost_layers_fixed, cost_layers_no_delete, cost_layers_open, purchase_lines, purchase_lines_item, purchase_lines_no_delete, purchase_lines_no_update (+50 more)

### Community 5 - "Community 5"
Cohesion: 0.06
Nodes (18): _digest(), history(), _key(), post_return(), PostedReturn, preview_book_total(), PurchaseReturnError, _require() (+10 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (18): _clean(), create(), Customer, CustomerError, delete(), _duplicate(), _from_row(), get() (+10 more)

### Community 7 - "Community 7"
Cohesion: 0.09
Nodes (25): book_snapshot(), cancel_closing(), close_day(), close_in_tx(), Closed, ClosingError, coverage(), Coverage (+17 more)

### Community 8 - "Community 8"
Cohesion: 0.10
Nodes (24): _check_key(), choices(), _clearing_zero(), current(), _digest(), _entry(), _header(), opening_of() (+16 more)

### Community 9 - "Community 9"
Cohesion: 0.09
Nodes (17): _digest(), distribute(), _key(), latest_distribution(), Posted, preview(), Preview, ProfitError (+9 more)

### Community 10 - "Community 10"
Cohesion: 0.07
Nodes (20): DotError, mfg_key(), parse_dot(), size_search_key(), mobile(), national_id(), suggest_item_code(), tire_size() (+12 more)

### Community 11 - "Community 11"
Cohesion: 0.10
Nodes (23): _account_ok(), add_partner(), close_and_hand_over(), _digest(), expense_accounts(), _key(), list_partners(), movement_info() (+15 more)

### Community 12 - "Community 12"
Cohesion: 0.12
Nodes (30): _active_managers(), audit(), AuthError, change_own_password(), check_new_user(), _check_username(), configured_permission_limit(), configured_permissions() (+22 more)

### Community 14 - "Community 14"
Cohesion: 0.11
Nodes (22): AdjustLine, _digest(), _entry_lines(), _gain_cost(), history(), _new_header(), post_adjustment(), PostedAdjustment (+14 more)

### Community 16 - "Community 16"
Cohesion: 0.12
Nodes (15): cancel(), _digest(), drifted(), gaps(), history(), _key(), latest(), program_balance() (+7 more)

### Community 17 - "Community 17"
Cohesion: 0.11
Nodes (32): cost_allocations, cost_allocations_issue, cost_allocations_layer, cost_allocations_no_delete, cost_allocations_no_update, journal_entries, journal_entries_no_delete, journal_entries_no_update (+24 more)

### Community 18 - "Community 18"
Cohesion: 0.12
Nodes (13): _installment_settlement(), InstallmentSettlement, _issue_parts(), _payload_hash(), post_return(), PostedReturn, preview_installment_refund(), _refund_method() (+5 more)

### Community 19 - "Community 19"
Cohesion: 0.15
Nodes (20): hash_password(), _b64(), _check_ring(), _derive(), fingerprint(), matches(), new_keyring(), open_vault() (+12 more)

### Community 20 - "Community 20"
Cohesion: 0.13
Nodes (16): applied_deposits(), cash_refunded(), collected_unrefunded(), customer_balance(), deposit_balance(), _digest(), history(), _key() (+8 more)

### Community 21 - "Community 21"
Cohesion: 0.11
Nodes (3): _cell_text(), ReportsPage, _Rows

### Community 24 - "Community 24"
Cohesion: 0.15
Nodes (22): BackupInfo, change_backup_password(), _checked_device_dek(), device_dek(), device_key_state(), health(), _keyring(), list_backups() (+14 more)

### Community 26 - "Community 26"
Cohesion: 0.15
Nodes (11): _clean(), create(), delete(), _duplicate(), get(), has_documents(), Supplier, SupplierError (+3 more)

### Community 27 - "Community 27"
Cohesion: 0.11
Nodes (5): FormBody, HeaderPlacement, InvoiceWorkspace, PageFrame, scroll_content()

### Community 29 - "Community 29"
Cohesion: 0.13
Nodes (3): PartyInfoStrip, PartyPopup, PartySearch

### Community 30 - "Community 30"
Cohesion: 0.14
Nodes (23): installment_contracts, installment_contracts_customer, installment_contracts_no_delete, installment_contracts_no_update, installment_rows, installment_rows_due, installment_rows_no_delete, installment_rows_no_update (+15 more)

### Community 31 - "Community 31"
Cohesion: 0.17
Nodes (16): check(), clean(), _clean_identity(), _clean_print(), load(), put(), _put_fields(), save() (+8 more)

### Community 33 - "Community 33"
Cohesion: 0.14
Nodes (24): cost_allocations, cost_allocations_issue, cost_allocations_layer, cost_allocations_no_delete, cost_allocations_no_update, cost_allocations_return, sale_return_lines, sale_return_lines_no_delete (+16 more)

### Community 34 - "Community 34"
Cohesion: 0.15
Nodes (23): cost_allocations, cost_allocations_issue, cost_allocations_layer, cost_allocations_no_delete, cost_allocations_no_update, cost_allocations_purchase_return, cost_allocations_return, purchase_return_lines (+15 more)

### Community 35 - "Community 35"
Cohesion: 0.13
Nodes (3): _extra_spec(), _ItemPopup, ItemSearch

### Community 36 - "Community 36"
Cohesion: 0.13
Nodes (18): app_meta, backup_runs, backup_runs_time, settings, audit_log, audit_log_time, backup_runs_new, backup_runs_time (+10 more)

### Community 37 - "Community 37"
Cohesion: 0.17
Nodes (20): accounts, customer_vehicles, customer_vehicles_customer, customer_vehicles_plate, customers, customers_name_norm, item_attrs, item_attrs_key_value (+12 more)

### Community 38 - "Community 38"
Cohesion: 0.20
Nodes (13): add_custom_field(), by_barcode(), _clean(), create(), delete(), get(), has_documents(), Item (+5 more)

### Community 39 - "Community 39"
Cohesion: 0.10
Nodes (5): DepositDialog, InterestRecognitionDialog, Modal, ReceiptDialog, ReverseDialog

### Community 40 - "Community 40"
Cohesion: 0.22
Nodes (12): _check_backing(), DepositError, _digest(), history(), _key(), _lines(), _post(), post_document() (+4 more)

### Community 42 - "Community 42"
Cohesion: 0.15
Nodes (11): _cannot_open(), main(), do_logout(), do_restore(), _scheduled(), _scheduled_done(), show_login(), _mark() (+3 more)

### Community 43 - "Community 43"
Cohesion: 0.18
Nodes (9): _apply_pragmas(), db_path(), _is_empty(), NestedTransaction, open_db(), open_reader(), read_app_id(), transaction() (+1 more)

### Community 44 - "Community 44"
Cohesion: 0.17
Nodes (14): _category(), collection_view(), CollectionView, CollectRow, ContractView, count(), get_contract(), invoice_for_contract() (+6 more)

### Community 45 - "Community 45"
Cohesion: 0.20
Nodes (11): blockers(), _digest(), _interest_blockers(), latest_lock(), lock_period(), Locked, LockError, _require() (+3 more)

### Community 46 - "Community 46"
Cohesion: 0.11
Nodes (5): AuditTab, NewUserDialog, RestoreConfirmDialog, ShowKeyDialog, _table()

### Community 47 - "Community 47"
Cohesion: 0.14
Nodes (3): arabic_latin_locale(), DateEdit, style_calendar()

### Community 48 - "Community 48"
Cohesion: 0.13
Nodes (9): ask(), chip(), confirm_discard_invoice(), form(), kbd(), kpi_tile(), muted(), Notice (+1 more)

### Community 50 - "Community 50"
Cohesion: 0.20
Nodes (10): collect_payment(), ContractError, Guarantor, _hash(), _payment_hash(), post_contract(), PostedContract, PostedPayment (+2 more)

### Community 52 - "Community 52"
Cohesion: 0.19
Nodes (8): default_through(), Item, month_end(), _periods(), post_ready(), PostResult, review(), _scheduled()

### Community 53 - "Community 53"
Cohesion: 0.22
Nodes (9): calculate(), EirCashflow, EirError, EirMonth, EirSchedule, _iso(), _month_end(), _next_month() (+1 more)

### Community 54 - "Community 54"
Cohesion: 0.23
Nodes (12): BackupError, BackupResult, copy_to_usb(), create_backup(), is_encryption_ready(), _now_s(), _record(), restore_test() (+4 more)

### Community 55 - "Community 55"
Cohesion: 0.21
Nodes (9): book_value(), _check_picks(), issue(), issue_in_tx(), IssueResult, _lots(), reconcile(), StockError (+1 more)

### Community 56 - "Community 56"
Cohesion: 0.21
Nodes (9): _check_customer(), _check_items(), _check_pricing(), next_number_preview(), _payload_hash(), post_sale(), PostedSale, SaleError (+1 more)

### Community 58 - "Community 58"
Cohesion: 0.18
Nodes (3): OpeningTab, ReverseOpeningDialog, SettingsPage

### Community 59 - "Community 59"
Cohesion: 0.17
Nodes (3): _Receiver, run_async(), _Worker

### Community 60 - "Community 60"
Cohesion: 0.20
Nodes (8): allocate(), fmt(), MoneyError, parse_money(), percent_of(), to_latin(), norm_ar(), scanner_fix()

### Community 62 - "Community 62"
Cohesion: 0.12
Nodes (5): party_field(), phone_query(), QuickPartyDialog, QuickCustomerDialog, _NewSupplierDialog

### Community 63 - "Community 63"
Cohesion: 0.16
Nodes (4): allocate(), check_payments(), Payment, SaleRuleError

### Community 64 - "Community 64"
Cohesion: 0.22
Nodes (10): count(), customer_stats(), get_sale(), _like(), list_sales(), refund_methods(), returnable_lines(), ReturnableLine (+2 more)

### Community 66 - "Community 66"
Cohesion: 0.24
Nodes (6): _dpapi(), forget_dek(), key_path(), KeystoreError, load_dek(), save_dek()

### Community 67 - "Community 67"
Cohesion: 0.23
Nodes (8): add_months(), _check_date(), _days_in_month(), Instalment, InstalmentError, markup_of(), Plan, plan()

### Community 68 - "Community 68"
Cohesion: 0.32
Nodes (7): _cashflows(), _day(), ModificationError, ModificationValuation, _money(), _present_value(), remeasure()

### Community 69 - "Community 69"
Cohesion: 0.33
Nodes (9): Alert, _collections_in(), DashboardView, _due_alerts(), get(), _old_dot_alert(), _period_start(), _safe_alert() (+1 more)

### Community 70 - "Community 70"
Cohesion: 0.22
Nodes (8): account_balance(), incomplete_entries(), LedgerError, locked_through(), open_entries(), post_entry(), unbalanced_entries(), valid_date()

### Community 71 - "Community 71"
Cohesion: 0.15
Nodes (5): _selfcheck(), ChangePasswordDialog, LoginDialog, dispose(), wait_all()

### Community 72 - "Community 72"
Cohesion: 0.28
Nodes (12): installment_return_adjustments, installment_return_adjustments_contract, installment_return_adjustments_no_delete, installment_return_adjustments_no_update, installment_return_allocations, installment_return_allocations_no_delete, installment_return_allocations_no_update, installment_return_allocations_row (+4 more)

### Community 73 - "Community 73"
Cohesion: 0.29
Nodes (12): profit_distribution_lines, profit_distribution_lines_no_delete, profit_distribution_lines_no_update, profit_distributions, profit_distributions_no_delete, profit_distributions_no_update, profit_share_lines, profit_share_lines_no_delete (+4 more)

### Community 74 - "Community 74"
Cohesion: 0.32
Nodes (8): applied(), _checksum(), current_version(), load_migrations(), migrate(), Migration, MigrationError, pending()

### Community 75 - "Community 75"
Cohesion: 0.22
Nodes (6): build(), Line, period_range(), Statement, _sums(), table_rows()

### Community 76 - "Community 76"
Cohesion: 0.23
Nodes (3): AdaptiveCards, AppearanceTab, ShortcutsTab

### Community 78 - "Community 78"
Cohesion: 0.19
Nodes (5): ltr_edit(), PasswordEdit, search_completer(), searchable_edit(), trail_icon()

### Community 79 - "Community 79"
Cohesion: 0.20
Nodes (4): parse_percent(), reserve_for(), ShareError, split()

### Community 80 - "Community 80"
Cohesion: 0.35
Nodes (9): _discard(), _fsync(), _fsync_dir(), _has_users(), _keep_damaged(), restore_file(), _settle_live_db(), _sidecars() (+1 more)

### Community 81 - "Community 81"
Cohesion: 0.32
Nodes (11): active_opening_lines, opening_balance_lines, opening_balance_lines_customer, opening_balance_lines_no_delete, opening_balance_lines_no_update, opening_balance_lines_supplier, opening_balances, opening_balances_no_delete (+3 more)

### Community 82 - "Community 82"
Cohesion: 0.21
Nodes (4): PostedReversal, ReversalError, reverse(), reversible()

### Community 83 - "Community 83"
Cohesion: 0.20
Nodes (3): _like(), lots(), summary()

### Community 84 - "Community 84"
Cohesion: 0.26
Nodes (7): account_names(), balances(), count(), EntryView, get_entry(), movements(), _where()

### Community 85 - "Community 85"
Cohesion: 0.21
Nodes (3): CustomerDocumentsDialog, ReverseDepositDialog, ReverseReceiptDialog

### Community 87 - "Community 87"
Cohesion: 0.22
Nodes (3): get_invoice(), InvoiceLine, InvoiceView

### Community 88 - "Community 88"
Cohesion: 0.22
Nodes (3): is_complete(), run_first_setup(), SetupError

### Community 89 - "Community 89"
Cohesion: 0.18
Nodes (6): action_row(), danger_button(), deferred_button(), icon_button(), menu_button(), primary_button()

### Community 90 - "Community 90"
Cohesion: 0.29
Nodes (7): apply(), available_fonts(), colors(), load_bundled_fonts(), palette(), pick_font(), qss()

### Community 94 - "Community 94"
Cohesion: 0.24
Nodes (4): ask(), Confirm, info(), Message

### Community 97 - "Community 97"
Cohesion: 0.27
Nodes (4): check_condition(), check_reason(), returnable(), ReturnRuleError

### Community 98 - "Community 98"
Cohesion: 0.38
Nodes (9): sale_lines, sale_lines_no_delete, sale_lines_no_update, sale_payments, sale_payments_no_delete, sale_payments_no_update, sales, sales_no_delete (+1 more)

### Community 105 - "Community 105"
Cohesion: 0.33
Nodes (5): InsufficientStock, plan_fifo(), plan_lots(), slice_cost(), take_units()

### Community 107 - "Community 107"
Cohesion: 0.44
Nodes (8): active_interest_recognitions, interest_recognition_reversals, interest_recognition_reversals_no_delete, interest_recognition_reversals_no_update, interest_recognitions, interest_recognitions_no_delete, interest_recognitions_no_update, interest_recognitions_period

### Community 114 - "Community 114"
Cohesion: 0.36
Nodes (4): Statement, statement(), StatementRow, unassigned()

### Community 116 - "Community 116"
Cohesion: 0.46
Nodes (7): installment_modifications, installment_modifications_contract, installment_modifications_no_delete, installment_modifications_no_update, installment_modified_cashflows, installment_modified_cashflows_no_delete, installment_modified_cashflows_no_update

### Community 117 - "Community 117"
Cohesion: 0.32
Nodes (3): acquire(), release_all(), _try_lock()

### Community 118 - "Community 118"
Cohesion: 0.50
Nodes (5): backups_dir(), data_dir(), db_path(), logs_dir(), looks_synced_or_network()

### Community 119 - "Community 119"
Cohesion: 0.43
Nodes (5): build(), Difference, _live_differences(), PartnerDebt, Report

### Community 123 - "Community 123"
Cohesion: 0.38
Nodes (4): field(), field_label(), kv_row(), ro_field()

### Community 124 - "Community 124"
Cohesion: 0.60
Nodes (5): cash_closings, cash_closings_date, cash_closings_no_delete, cash_closings_no_update, cash_closings_one_per_date

### Community 129 - "Community 129"
Cohesion: 0.70
Nodes (4): safe_counts, safe_counts_no_delete, safe_counts_no_update, safe_counts_one_per_date

### Community 130 - "Community 130"
Cohesion: 0.60
Nodes (4): journal_entries_period_locked, period_locks, period_locks_no_delete, period_locks_no_update

### Community 131 - "Community 131"
Cohesion: 0.70
Nodes (4): customer_deposits, customer_deposits_no_delete, customer_deposits_no_update, idx_customer_deposits_customer

### Community 134 - "Community 134"
Cohesion: 0.83
Nodes (4): all_fields(), Field, fields(), visible_fields()

## Knowledge Gaps
- **60 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Modal` connect `Community 39` to `Community 1`, `Community 5`, `Community 6`, `Community 135`, `Community 8`, `Community 9`, `Community 11`, `Community 14`, `Community 16`, `Community 18`, `Community 26`, `Community 41`, `Community 46`, `Community 48`, `Community 58`, `Community 62`, `Community 71`, `Community 85`, `Community 86`, `Community 91`, `Community 94`, `Community 96`, `Community 102`, `Community 110`, `Community 111`, `Community 113`, `Community 121`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Are the 14 inferred relationships involving `PosPage` (e.g. with `MoneyError` and `SaleError`) actually correct?**
  _`PosPage` has 14 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 0` be split into smaller, more focused modules?**
  _Cohesion score 0.056692242114237 - nodes in this community are weakly interconnected._
- **Why does `ListPage` connect `Community 28` to `Community 65`, `Community 5`, `Community 6`, `Community 103`, `Community 109`, `Community 13`, `Community 49`, `Community 51`, `Community 21`, `Community 22`, `Community 26`, `Community 92`, `Community 93`, `Community 127`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `Modal` (e.g. with `Notice` and `BackupTab`) actually correct?**
  _`Modal` has 2 INFERRED edges - model-reasoned connections that need verification._
- **Should `Community 1` be split into smaller, more focused modules?**
  _Cohesion score 0.07459505541346974 - nodes in this community are weakly interconnected._
- **Why does `PosPage` connect `Community 0` to `Community 128`, `Community 3`, `Community 35`, `Community 104`, `Community 13`, `Community 48`, `Community 22`, `Community 56`, `Community 27`, `Community 60`, `Community 29`?**
  _High betweenness centrality (0.052) - this node is a cross-community bridge._