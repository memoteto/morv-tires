"""Builds every printed document from the approved design in design/Shop.dc.html, for sheet and roll."""
from dataclasses import dataclass, field

from PySide6.QtCore import QMarginsF, QSizeF, Qt
from PySide6.QtGui import QFont, QImage, QPageLayout, QPageSize, QPainter, QTextDocument
from PySide6.QtPrintSupport import QPrinter

from morv.core.money import fmt
from morv.ui import icons, theme

BLUE = "#1F5FD6"
INK = "#1F2937"
MUTED = "#4B5563"
LINE = "#E1E6EE"
FILL = "#F3F5F9"
DASH = "#C9D2DF"


@dataclass(frozen=True)
class Shape:
    key: str
    label: str
    width_mm: float
    height_mm: float
    margin_mm: float
    scale: float                     # type size against the A4 sheet, which the design draws at 1.0
    roll: bool = False


SHAPES = {
    "a4": Shape("a4", "A4", 210, 297, 12, 1.0),
    "a5": Shape("a5", "A5", 148, 210, 10, 0.86),
    "r80": Shape("r80", "رول 80مم", 80, 297, 4, 0.78, roll=True),
    "r58": Shape("r58", "رول 58مم", 58, 297, 3, 0.68, roll=True),
}


@dataclass
class Line:
    name: str
    detail: str = ""                 # the design prints it next to the name after a middle dot
    qty: str = ""
    price: int = 0
    total: int = 0


@dataclass
class Document:
    title: str
    number: str
    date: str
    info: list[tuple[str, str]] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    totals: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""                   # stands in for the items table on documents that have no lines


def _pt(shape: Shape, size: float) -> str:
    return f"{round(size * shape.scale, 1)}pt"


def _ltr(text: str) -> str:
    """Isolates a Latin or numeric value so an RTL paragraph cannot reorder its digits."""
    return f"<span dir='ltr'>{text}</span>"


def _icon(name: str, color: str, size: int) -> str:
    return f"<img src='file://{icons.svg_file(name, color)}' width='{size}' height='{size}'>"


def _logo_cell(shop, size: int) -> str:
    path = shop.logo_path
    if path:
        return f"<img src='file://{path.as_posix()}' width='{size}' height='{size}'>"
    return (f"<table width='{size}' cellspacing='0' cellpadding='0' bgcolor='{BLUE}'><tr>"
            f"<td align='center' style='color:#FFFFFF;font-weight:bold;font-size:{size * 3 // 5}pt'>"
            f"{shop.initial}</td></tr></table>")


def _sheet_head(shop, doc: Document, shape: Shape) -> str:
    tax = ""
    if shop.tax_registered and (shop.commercial_register or shop.tax_id):
        parts = []
        if shop.commercial_register:
            parts.append(f"س.ت: {_ltr(shop.commercial_register)}")
        if shop.tax_id:
            parts.append(f"رقم التسجيل الضريبي: {_ltr(shop.tax_id)}")
        tax = (f"<div style='color:{MUTED};font-size:{_pt(shape, 8)}'>"
               f"{'&nbsp;&nbsp;&nbsp;'.join(parts)}</div>")
    logo = ""
    if shop.show_logo:
        logo = f"<td width='44' valign='middle'>{_logo_cell(shop, 38)}</td>"
    name = (f"<td valign='middle' dir='rtl'>"
            f"<div style='font-size:{_pt(shape, 14)};font-weight:bold;color:{INK}'>{shop.name}</div>"
            f"<div style='color:{MUTED};font-size:{_pt(shape, 8.5)}'>{shop.activity}</div>{tax}</td>")
    doc_block = (f"<td width='34%' valign='middle' align='left' dir='ltr'>"
                 f"<div align='left' style='color:{BLUE};font-weight:bold;font-size:{_pt(shape, 12)}'>"
                 f"{doc.title}</div>"
                 f"<div align='left' style='color:{INK};font-size:{_pt(shape, 9.5)}'>{doc.number}</div>"
                 f"<div align='left' style='color:{MUTED};font-size:{_pt(shape, 9)}'>{doc.date}</div></td>")
    # The wrapper runs LTR so the cells land where the design puts them whatever Qt does with RTL tables.
    return (f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr'><tr>{doc_block}"
            f"<td valign='middle'><table cellspacing='0' cellpadding='0' dir='ltr'><tr>{name}{logo}"
            f"</tr></table></td></tr></table>"
            f"<div style='border-bottom:2px solid {BLUE};font-size:2pt'>&nbsp;</div>")


def _info_box(doc: Document, shape: Shape) -> str:
    if not doc.info:
        return ""
    rows = ""
    for i in range(0, len(doc.info), 2):
        pair = doc.info[i:i + 2]
        cells = ""
        for label, value in reversed(pair):      # emitted left to right, so the first pair lands on the right
            cells += (f"<td width='50%' dir='rtl' style='font-size:{_pt(shape, 9)}'>"
                      f"<span style='color:{MUTED}'>{label}:</span> "
                      f"<span style='color:{INK}'>{value}</span></td>")
        if len(pair) == 1:
            cells = "<td width='50%'></td>" + cells
        rows += f"<tr>{cells}</tr>"
    return (f"<table width='100%' cellspacing='0' cellpadding='4' bgcolor='{FILL}' dir='ltr'"
            f" style='margin-top:6px'>{rows}</table>")


_COLS = (("الإجمالي", "11%"), ("السعر", "11%"), ("الكمية", "8%"), ("الصنف", "64%"), ("م", "6%"))


def _lines_table(doc: Document, shape: Shape) -> str:
    if not doc.lines:
        return ""
    head = "".join(f"<td width='{w}' align='center' style='padding:4px;color:{MUTED};font-weight:bold'>{t}</td>"
                   for t, w in _COLS)
    body = ""
    for i, line in enumerate(doc.lines, 1):
        name = line.name + (f" <span style='color:{MUTED}'>&#183; {line.detail}</span>" if line.detail else "")
        body += (f"<tr>"
                 f"<td align='left' dir='ltr' style='padding:4px'>{fmt(line.total)}</td>"
                 f"<td align='left' dir='ltr' style='padding:4px'>{fmt(line.price)}</td>"
                 f"<td align='center' dir='ltr' style='padding:4px'>{line.qty}</td>"
                 f"<td dir='rtl' style='padding:4px'>{name}</td>"
                 f"<td align='center' dir='ltr' style='padding:4px;color:{MUTED}'>{i}</td></tr>")
    # Columns are emitted in visual left-to-right order inside an LTR table: Qt then puts
    # the serial on the right and the total on the left, as the design shows them.
    return (f"<table width='100%' cellspacing='0' cellpadding='0' border='1' dir='ltr'"
            f" style='margin-top:8px;border-color:{LINE};font-size:{_pt(shape, 9.5)}'>"
            f"<tr bgcolor='{FILL}'>{head}</tr>{body}</table>")


def _totals_block(doc: Document, shape: Shape) -> str:
    if not doc.totals:
        return ""
    rows = ""
    for i, (label, value) in enumerate(doc.totals):
        last = i == len(doc.totals) - 1
        style = (f"color:{BLUE};font-weight:bold;font-size:{_pt(shape, 11.5)};"
                 f"border-top:1px solid {LINE}") if last else f"color:{INK};font-size:{_pt(shape, 9.5)}"
        rows += (f"<tr><td align='left' dir='ltr' style='padding:2px 4px;{style}'>{value}</td>"
                 f"<td align='right' dir='rtl' style='padding:2px 4px;{style}'>{label}</td></tr>")
    inner = f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr'>{rows}</table>"
    return (f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr' style='margin-top:8px'><tr>"
            f"<td width='48%'>{inner}</td><td width='52%'></td></tr></table>")


def _sheet_foot(shop, doc: Document, shape: Shape) -> str:
    out = ""
    if doc.note:
        out += (f"<div dir='rtl' style='margin-top:8px;color:{INK};font-size:{_pt(shape, 9.5)}'>"
                f"{doc.note}</div>")
    if shop.thanks:
        out += (f"<div align='center' style='margin-top:10px;color:{BLUE};font-weight:bold;"
                f"font-size:{_pt(shape, 10)}'>{shop.thanks}</div>")
    if shop.show_policy and shop.policy:
        out += (f"<div dir='rtl' style='margin-top:6px;border-top:1px dashed {DASH};padding-top:4px;"
                f"color:{MUTED};font-size:{_pt(shape, 8)}'>{shop.policy.replace(chr(10), '<br>')}</div>")
    items = []
    if shop.address:
        items.append(("pin", shop.address))
    for phone in (shop.phone, shop.phone2):
        if phone:
            items.append(("phone", _ltr(phone)))
    if shop.show_whatsapp and shop.whatsapp:
        items.append(("chat", _ltr(shop.whatsapp)))
    if shop.show_hours and shop.hours:
        items.append(("clock", shop.hours))
    if items:
        size = max(8, round(9 * shape.scale))
        cells = "&nbsp;&nbsp;&nbsp;&nbsp;".join(
            f"{_icon(name, MUTED, size)}&nbsp;{text}" for name, text in items)
        out += (f"<div align='center' dir='rtl' style='margin-top:12px;border-top:2px solid {INK};"
                f"padding-top:6px;color:{INK};font-size:{_pt(shape, 8.5)}'>{cells}</div>")
    return out


def _roll(shop, doc: Document, shape: Shape) -> str:
    def rule() -> str:
        return "<div style='border-bottom:1px dashed #9CA3AF;font-size:2pt;margin:4px 0'>&nbsp;</div>"

    def row(label: str, value: str, strong: bool = False) -> str:
        weight = "font-weight:bold;" if strong else ""
        size = _pt(shape, 12) if strong else _pt(shape, 9.5)
        return (f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr'><tr>"
                f"<td align='left' dir='ltr' style='{weight}font-size:{size}'>{value}</td>"
                f"<td align='right' dir='rtl' style='{weight}font-size:{size}'>{label}</td></tr></table>")

    out = f"<div align='center' style='font-weight:bold;font-size:{_pt(shape, 13)};color:{INK}'>{shop.name}</div>"
    if shop.activity:
        out += f"<div align='center' style='color:{MUTED};font-size:{_pt(shape, 8.5)}'>{shop.activity}</div>"
    if shop.phone:
        out += f"<div align='center' style='font-size:{_pt(shape, 9)}'>{_ltr(shop.phone)}</div>"
    if shop.tax_registered and shop.tax_id:
        out += (f"<div align='center' style='color:{MUTED};font-size:{_pt(shape, 8)}'>"
                f"رقم التسجيل الضريبي: {_ltr(shop.tax_id)}</div>")
    out += rule()
    out += f"<div align='center' style='font-weight:bold;font-size:{_pt(shape, 10.5)}'>{doc.title}</div>"
    out += f"<div align='center' dir='ltr' style='font-size:{_pt(shape, 9)}'>{doc.number}</div>"
    out += f"<div align='center' dir='ltr' style='color:{MUTED};font-size:{_pt(shape, 9)}'>{doc.date}</div>"
    if doc.info:
        out += rule()
        for label, value in doc.info:
            out += row(label, value)
    if doc.lines:
        out += rule()
        for line in doc.lines:
            name = line.name + (f" &#183; {line.detail}" if line.detail else "")
            out += (f"<div dir='rtl' style='font-size:{_pt(shape, 9.5)}'>{name}</div>")
            out += row(f"{line.qty} &#215; {fmt(line.price)}", fmt(line.total))
    if doc.totals:
        out += rule()
        for i, (label, value) in enumerate(doc.totals):
            out += row(label, value, strong=i == len(doc.totals) - 1)
    if doc.note:
        out += rule()
        out += f"<div dir='rtl' style='font-size:{_pt(shape, 9.5)}'>{doc.note}</div>"
    if shop.thanks:
        out += rule()
        out += f"<div align='center' style='font-weight:bold;font-size:{_pt(shape, 9.5)}'>{shop.thanks}</div>"
    if shop.show_policy and shop.policy:
        out += (f"<div align='center' dir='rtl' style='margin-top:4px;color:{MUTED};"
                f"font-size:{_pt(shape, 8)}'>{shop.policy.replace(chr(10), '<br>')}</div>")
    if shop.show_hours and shop.hours:
        out += f"<div align='center' style='margin-top:4px;font-size:{_pt(shape, 8.5)}'>{shop.hours}</div>"
    return out


def document_html(shop, doc: Document, shape: Shape) -> str:
    if shape.roll:
        body = _roll(shop, doc, shape)
    else:
        body = (_sheet_head(shop, doc, shape) + _info_box(doc, shape) + _lines_table(doc, shape)
                + _totals_block(doc, shape) + _sheet_foot(shop, doc, shape))
    return (f"<html><body dir='rtl' bgcolor='#FFFFFF' style='color:{INK}'>{body}</body></html>")


def print_font(shape: Shape) -> QFont:
    """Paper must name its own Arabic family: unlike a window, it never inherits the application font."""
    font = QFont(theme.pick_font().family())
    font.setPointSizeF(10.5 * shape.scale)
    return font


def page_layout(shape: Shape) -> QPageLayout:
    size = QPageSize(QSizeF(shape.width_mm, shape.height_mm), QPageSize.Unit.Millimeter, shape.label,
                     QPageSize.SizeMatchPolicy.FuzzyMatch)
    margin = QMarginsF(shape.margin_mm, shape.margin_mm, shape.margin_mm, shape.margin_mm)
    return QPageLayout(size, QPageLayout.Orientation.Portrait, margin, QPageLayout.Unit.Millimeter)


def render(shop, doc: Document, shape: Shape) -> QTextDocument:
    layout = page_layout(shape)
    text = QTextDocument()
    text.setDefaultFont(print_font(shape))
    text.setPageSize(layout.paintRectPixels(96).size().toSizeF())
    text.setHtml(document_html(shop, doc, shape))
    return text


def preview_image(shop, doc: Document, shape: Shape, width_px: int = 0) -> QImage:
    """Draws the paper at its true proportions so the preview and the printed sheet cannot drift apart."""
    real_px = max(1, round((shape.width_mm - 2 * shape.margin_mm) / 25.4 * 96))
    text = render(shop, doc, shape)
    text.setTextWidth(real_px)
    image = QImage(real_px, max(1, round(text.size().height())), QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    text.drawContents(painter)
    painter.end()
    if width_px and width_px != real_px:
        return image.scaledToWidth(width_px, Qt.TransformationMode.SmoothTransformation)
    return image


def sample_document() -> Document:
    """One fake document, shared by the settings preview and the sample sheets."""
    return Document(
        title="فاتورة بيع", number="S-2026-000154", date="2026-11-22 18:40",
        info=[("العميل", "أحمد محمود رمضان"), ("الموبايل", "01000000021"),
              ("المركبة", "تويوتا كورولا · ق س ط 4826"), ("الدفع", "نقدي")],
        lines=[Line("إطار ميشلان Primacy 4", "205/55R16 · 91V", "2", 645000, 1290000),
               Line("إطار بريدجستون Ecopia EP150", "195/65R15 · 91H", "4", 520000, 2080000),
               Line("بطارية ACDelco 70 أمبير", "ضمان حتى 2027-11-22", "1", 380000, 380000)],
        totals=[("الإجمالي", fmt(3750000)), ("ض.ق.م 14%", fmt(525000)),
                ("الصافي", f"{fmt(4275000)} ج.م")])


def to_printer(shop, doc: Document, printer: QPrinter, shape: Shape) -> None:
    printer.setPageLayout(page_layout(shape))
    render(shop, doc, shape).print_(printer)


def write_pdf(shop, doc: Document, path: str, shape_key: str = "a4") -> None:
    shape = SHAPES[shape_key]
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    to_printer(shop, doc, printer, shape)
