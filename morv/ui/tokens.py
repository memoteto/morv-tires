"""Approved design-system colours v1.0, light and dark."""

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
    preview_min=360, preview_max=420, list_min=640,
    search_min=190, filter_min=95, field_label_max=160, field_spacing=10,
)
