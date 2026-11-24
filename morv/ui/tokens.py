"""Approved design-system colours v1.0, light and dark."""

# Icon tints. One tint serves both themes on purpose: a page keeps the icons it was built with,
# and only the shell retints its own on a theme switch, so a theme-bound tint would go stale.
ICON_MUTED = "#8792A3"
ICON_PRIMARY = "#1F5FD6"
ICON_DANGER = "#B42323"
ICON_ON_PRIMARY = "#FFFFFF"

LIGHT = dict(
    bg="#F3F5F9", surface="#FFFFFF", surface2="#F6F8FB", border="#E1E6EE", border_strong="#8792A3",
    text="#0F172A", muted="#566173", primary="#1F5FD6", primary_soft="#E8F0FE", on_primary="#FFFFFF",
    danger="#B42323", danger_soft="#FDECEC", success="#0B7A48", success_soft="#E3F5EC",
    warn="#9A6700", warn_soft="#FFF3D6",
    side="#0D1A33", side_text="#C3CEE2", side_hover="#16274A", select="#C9DAFB",
)

DARK = dict(
    bg="#0A1120", surface="#111A2C", surface2="#16223A", border="#24314B", border_strong="#5A6A87",
    text="#E7EDF6", muted="#9AA7BC", primary="#2F6BE0", primary_soft="#162A50", on_primary="#FFFFFF",
    danger="#FF8A8A", danger_soft="#3A1717", success="#4CD394", success_soft="#0F2E22",
    warn="#F0B94A", warn_soft="#33270D",
    side="#070C17", side_text="#C3CEE2", side_hover="#111C33", select="#21407A",
)

# Logical Qt pixels shared by all list-preview screens.
LAYOUT = dict(
    page_margin=24, pane_gap=12, preview_padding=16,
    page_margin_y=16, page_header_min=56, page_footer_min=48,
    page_back_min=38, page_back_icon=18, page_back_gap=8,
    shop_logo_preview=80, form_field_min=180, print_policy_min_height=80,
    print_preview_min_height=300, print_preview_min_width=170,
    settings_card_min=320, settings_group_header_min=54, settings_link_min=44,
    preview_scroll_gutter=14, preview_scroll_end_gap=4,
    preview_min=380, preview_max=540, preview_share_pct=41, list_min=640,
    search_min=190, filter_min=95, field_label_max=160, field_spacing=10,
    summary_field_stack_below=300, summary_field_label_min=80,
    summary_field_value_min=100, summary_record_columns=3,
    summary_table_min_column=72, summary_table_max_column=250,
    summary_table_row_height=36, summary_table_datetime_row_height=52, summary_table_visible_rows=6,
    summary_table_cards_below=580, summary_cards_min_height=144,
    summary_cards_max_height=340, summary_cards_row_height=148,
)
