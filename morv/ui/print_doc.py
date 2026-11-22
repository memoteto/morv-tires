"""One drawing for every printed document, in a wide shape (A4/A5) and a narrow one (80/58mm rolls)."""
from dataclasses import dataclass, field
from html import escape

from PySide6.QtCore import QMarginsF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

from morv.core.money import fmt
from morv.core.text import ltr
from morv.ui.report_export import _rich

# Space the shop fills with its own words; it prints empty until they write something, so the
# paper always leaves the same room and a later footer does not shift the rest of the page.
FOOTER_MIN_HEIGHT_MM = 18


@dataclass(frozen=True)
class Shape:
    """A paper the shop prints on. Rolls have no fixed length, so they get a tall page and one margin."""
    key: str
    label: str
    width_mm: float
    height_mm: float
    margin_mm: float
    narrow: bool = False


SHAPES = {s.key: s for s in (
    Shape("a4", "A4", 210, 297, 12),
    Shape("a5", "A5", 148, 210, 10),
    Shape("80mm", "رول 80مم", 80, 300, 3, narrow=True),
    Shape("58mm", "رول 58مم", 58, 300, 3, narrow=True),
)}
DEFAULT_SHAPE = "a4"

_WIDE_STYLE = """
body { font-size: 10.5pt; }
.shop { font-size: 15pt; font-weight: bold; }
.shop-line { font-size: 9pt; color: #333333; }
.title { font-size: 13pt; font-weight: bold; }
.meta td { padding: 1px 0; font-size: 9.5pt; }
table.lines { width: 100%; border-collapse: collapse; margin-top: 6px; }
table.lines th { background: #e8ecf3; border: 1px solid #b8c0cc; padding: 4px 6px; font-size: 9.5pt; }
table.lines td { border: 1px solid #b8c0cc; padding: 4px 6px; }
table.totals { margin-top: 8px; }
table.totals td { padding: 2px 10px; }
table.totals tr.grand td { font-weight: bold; font-size: 12pt; border-top: 2px solid #333333; }
.footer { border-top: 1px solid #b8c0cc; margin-top: 10px; padding-top: 6px; font-size: 9pt; color: #333333; }
"""

# A roll is too narrow for columns, so every line becomes its own small block of stacked rows.
_NARROW_STYLE = """
body { font-size: 8.5pt; }
.shop { font-size: 11pt; font-weight: bold; }
.shop-line { font-size: 7.5pt; }
.title { font-size: 10pt; font-weight: bold; }
.meta td { padding: 0; font-size: 7.5pt; }
table.lines { width: 100%; margin-top: 4px; }
table.lines th { display: none; }
table.lines td { padding: 1px 0; border-bottom: 1px dotted #999999; }
table.totals { width: 100%; margin-top: 4px; }
table.totals td { padding: 1px 0; }
table.totals tr.grand td { font-weight: bold; font-size: 10pt; border-top: 1px solid #000000; }
.footer { border-top: 1px dashed #999999; margin-top: 6px; padding-top: 4px; font-size: 7.5pt; }
"""


@dataclass(frozen=True)
class Column:
    head: str
    width: str = ""
    money: bool = False


@dataclass
class Document:
    """A printable document: what it is, who it is with, its lines and its totals."""
    title: str
    number: str
    date: str
    parties: list = field(default_factory=list)      # (label, value) shown under the title
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)         # each row matches columns, money cells already int
    totals: list = field(default_factory=list)       # (label, amount, is_grand)
    notes: str = ""


def _cell(value, col: Column) -> str:
    return ltr(fmt(value)) if col.money and isinstance(value, int) else str(value)


def _shop_header(shop) -> str:
    """Tax and register numbers print only once the shop is registered and has filled them in."""
    ids = []
    if shop.tax_id:
        ids.append(f"رقم ضريبي: {ltr(shop.tax_id)}")
    if shop.commercial_register:
        ids.append(f"سجل تجاري: {ltr(shop.commercial_register)}")
    lines = [f"<div class='shop'>{escape(shop.name)}</div>"]
    contact = " · ".join(x for x in (ltr(shop.phone), shop.address) if x)
    if contact:
        lines.append(f"<div class='shop-line'>{_rich(contact)}</div>")
    if ids:
        lines.append(f"<div class='shop-line'>{_rich(' · '.join(ids))}</div>")
    return "".join(lines)


def _wide_lines(doc: Document) -> str:
    heads = "".join(f"<th{f' width={c.width}' if c.width else ''}>{escape(c.head)}</th>" for c in doc.columns)
    body = []
    for row in doc.rows:
        cells = "".join(f"<td>{_rich(_cell(v, c))}</td>" for v, c in zip(row, doc.columns, strict=True))
        body.append(f"<tr>{cells}</tr>")
    return f"<table class='lines'><tr>{heads}</tr>{''.join(body)}</table>"


def _narrow_lines(doc: Document) -> str:
    """On a roll each line prints as its own block: the first column alone, then the rest labelled."""
    blocks = []
    for row in doc.rows:
        first, *rest = [(c, _cell(v, c)) for v, c in zip(row, doc.columns, strict=True)]
        detail = " · ".join(f"{c.head} {v}" for c, v in rest if str(v).strip())
        blocks.append(f"<tr><td>{_rich(first[1])}<br><span class='shop-line'>{_rich(detail)}</span></td></tr>")
    return f"<table class='lines'>{''.join(blocks)}</table>"


def document_html(shop, doc: Document, shape: Shape) -> str:
    totals = "".join(
        f"<tr class='{'grand' if grand else ''}'><td>{escape(label)}</td>"
        f"<td align='left'>{_rich(ltr(fmt(amount)))}</td></tr>"
        for label, amount, grand in doc.totals)
    meta = "".join(f"<tr><td>{escape(label)}</td><td>&nbsp;{_rich(str(value))}</td></tr>"
                   for label, value in doc.parties)
    note = f"<div class='shop-line'>{_rich(doc.notes)}</div>" if doc.notes else ""
    footer = _rich(shop.invoice_footer) if shop.invoice_footer else "&nbsp;"
    style = _NARROW_STYLE if shape.narrow else _WIDE_STYLE
    head = (f"<div class='title'>{escape(doc.title)} {_rich(ltr(doc.number))}</div>"
            f"<div class='shop-line'>{_rich(ltr(doc.date))}</div>" if shape.narrow else
            f"<table width='100%'><tr><td><span class='title'>{escape(doc.title)} {_rich(ltr(doc.number))}</span></td>"
            f"<td align='left' class='shop-line'>{_rich(ltr(doc.date))}</td></tr></table>")
    totals_table = (f"<table class='totals'>{totals}</table>" if shape.narrow
                    else f"<table class='totals' align='left'>{totals}</table><div style='clear: both'></div>")
    return (f"<html><head><style>{style}</style></head><body dir='rtl'>"
            f"{_shop_header(shop)}<hr>{head}"
            f"<table class='meta'>{meta}</table>"
            f"{_narrow_lines(doc) if shape.narrow else _wide_lines(doc)}"
            f"{totals_table}{note}"
            f"<div class='footer' style='min-height: {FOOTER_MIN_HEIGHT_MM}mm'>{footer}</div>"
            f"</body></html>")


def _page_size(shape: Shape) -> QPageSize:
    if shape.key == "a4":
        return QPageSize(QPageSize.PageSizeId.A4)
    if shape.key == "a5":
        return QPageSize(QPageSize.PageSizeId.A5)
    return QPageSize(QSizeF(shape.width_mm, shape.height_mm), QPageSize.Unit.Millimeter, shape.label)


def render(shop, doc: Document, shape: Shape) -> QTextDocument:
    """The one drawing both the PDF and the printer use, so paper and file can never drift apart."""
    text = QTextDocument()
    text.setHtml(document_html(shop, doc, shape))
    return text


def write_pdf(shop, doc: Document, path: str, shape_key: str = DEFAULT_SHAPE) -> None:
    shape = SHAPES[shape_key]
    writer = QPdfWriter(path)
    writer.setPageSize(_page_size(shape))
    writer.setPageMargins(QMarginsF(shape.margin_mm, shape.margin_mm, shape.margin_mm, shape.margin_mm),
                          QPageLayout.Unit.Millimeter)
    text = render(shop, doc, shape)
    text.setPageSize(writer.pageLayout().paintRectPixels(writer.resolution()).size().toSizeF())
    text.print_(writer)
