"""Builds every printed document from the approved design in design/Shop.dc.html, for sheet and roll."""
import re
from dataclasses import dataclass, field

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
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
ICON_URL = "morv:icon/"       # a file:// image never loads in a QTextDocument
LOGO_URL = "morv:logo"
FOOT_ICONS = ("pin", "phone", "chat", "clock")


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


_ARABIC = re.compile(r"[\u0600-\u06FF]")
_LATIN = re.compile(r"[0-9A-Za-z]")


# A date or a code reverses segment by segment inside an Arabic line: 2027-11-22 prints as 22-11-2027
_GROUPED = re.compile(r"[0-9A-Za-z]+(?:[-/:.,][0-9A-Za-z]+)+")


def _hold_order(text: str) -> str:
    """Pins a value built of groups and separators left to right, whatever surrounds it."""
    return _GROUPED.sub(lambda m: f"<span dir='ltr'>{m.group(0)}</span>", text)


def _keep_spaces(text: str) -> str:
    """Printing on Windows swallows a space where Arabic meets Latin; a non-breaking one survives."""
    out = []
    for i, ch in enumerate(text):
        if ch == " " and 0 < i < len(text) - 1:
            before, after = text[i - 1], text[i + 1]
            if ((_ARABIC.match(before) and _LATIN.match(after))
                    or (_LATIN.match(before) and _ARABIC.match(after))):
                out.append("\u00a0")
                continue
        out.append(ch)
    return "".join(out)


def _shell(inner: str) -> str:
    # Only the text between tags is touched, never an attribute
    inner = re.sub(r">([^<]+)<", lambda m: ">" + _hold_order(_keep_spaces(m.group(1))) + "<", inner)
    return f"<html><body dir='rtl' bgcolor='#FFFFFF' style='color:{INK}'>{inner}</body></html>"


def _gap(height_pt: float) -> str:
    """Vertical space as an empty row: Qt's print engine ignores a margin on a block."""
    return (f"<table width='100%' cellspacing='0' cellpadding='0'>"
            f"<tr><td style='font-size:{height_pt}pt'>&nbsp;</td></tr></table>")


def _band(color: str, height_pt: float = 0.8) -> str:
    """A filled one-cell table: Qt's print engine ignores a CSS border on a block, but paints this."""
    return (f"<table width='100%' cellspacing='0' cellpadding='0' bgcolor='{color}'>"
            f"<tr><td style='font-size:{height_pt}pt'>&nbsp;</td></tr></table>")


def _icon(name: str, size: int) -> str:
    return f"<img src='{ICON_URL}{name}' width='{size}' height='{size}'>"


def _logo_cell(shop, size: int) -> str:
    if shop.logo_path:
        return f"<img src='{LOGO_URL}' width='{size}' height='{size}'>"
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
        tax = (f"<div align='right' style='color:{MUTED};font-size:{_pt(shape, 8)}'>"
               f"{'&nbsp;&nbsp;&nbsp;'.join(parts)}</div>")
    logo = ""
    if shop.show_logo:
        logo = f"<td width='42' valign='middle' align='left'>{_logo_cell(shop, 38)}</td>"
    name = (f"<td valign='middle' align='right' dir='rtl' style='padding-right:10px'>"
            f"<div align='right' style='font-size:{_pt(shape, 14)};font-weight:bold;color:{INK}'>"
            f"{shop.name}</div>"
            f"<div align='right' style='color:{MUTED};font-size:{_pt(shape, 8.5)}'>{shop.activity}</div>"
            f"{tax}</td>")
    doc_block = (f"<td width='32%' valign='middle' align='left' dir='ltr'>"
                 f"<div align='left' style='color:{BLUE};font-weight:bold;font-size:{_pt(shape, 12)}'>"
                 f"{doc.title}</div>"
                 f"<div align='left' style='color:{INK};font-size:{_pt(shape, 9.5)}'>{doc.number}</div>"
                 f"<div align='left' style='color:{MUTED};font-size:{_pt(shape, 9)}'>{doc.date}</div></td>")
    # The wrapper runs LTR so the cells land where the design puts them whatever Qt does with RTL tables.
    return (f"<table width='100%' cellspacing='0' cellpadding='2' dir='ltr'>"
            f"<tr>{doc_block}{name}{logo}</tr></table>{_band(BLUE, 1.4)}")


def _info_box(doc: Document, shape: Shape) -> str:
    if not doc.info:
        return ""
    rows = ""
    for i in range(0, len(doc.info), 2):
        pair = doc.info[i:i + 2]
        cells = ""
        for label, value in reversed(pair):      # emitted left to right, so the first pair lands on the right
            # Label and value sit in cells of their own, and a right-to-left mark after the colon
            # keeps that neutral character beside its label instead of at the far edge of the line
            cells += (f"<td width='37%' align='right' dir='rtl' style='color:{INK};"
                      f"font-size:{_pt(shape, 9)}'>{value}</td>"
                      f"<td width='13%' align='right' dir='rtl' style='color:{MUTED};"
                      f"font-size:{_pt(shape, 9)}'>{label}:&#8207;&nbsp;</td>")
        if len(pair) == 1:
            cells = "<td width='37%'></td><td width='13%'></td>" + cells
        rows += f"<tr>{cells}</tr>"
    return _gap(4) + (f"<table width='100%' cellspacing='0' cellpadding='4' bgcolor='{FILL}' "
                      f"dir='ltr'>{rows}</table>")


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
    return _gap(5) + (f"<table width='100%' cellspacing='0' cellpadding='0' border='1' dir='ltr'"
                      f" style='border-color:{LINE};font-size:{_pt(shape, 9.5)}'>"
                      f"<tr bgcolor='{FILL}'>{head}</tr>{body}</table>")


def _totals_block(doc: Document, shape: Shape) -> str:
    if not doc.totals:
        return ""
    rows = ""
    for i, (label, value) in enumerate(doc.totals):
        last = i == len(doc.totals) - 1
        style = (f"color:{BLUE};font-weight:bold;font-size:{_pt(shape, 11.5)}" if last
                 else f"color:{INK};font-size:{_pt(shape, 9.5)}")
        if last:
            rows += f"<tr><td colspan='2' style='padding:0'>{_band(LINE)}</td></tr>"
        # The value keeps the paragraph's own direction so a currency word stays where the design puts it
        rows += (f"<tr><td align='left' dir='rtl' style='padding:2px 4px;{style}'>{value}</td>"
                 f"<td align='right' dir='rtl' style='padding:2px 4px;{style}'>{label}</td></tr>")
    inner = f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr'>{rows}</table>"
    return _gap(5) + (f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr'><tr>"
                      f"<td width='48%'>{inner}</td><td width='52%'></td></tr></table>")


def _sheet_tail(shop, doc: Document, shape: Shape) -> str:
    out = ""
    if doc.note:
        out += _gap(5) + (f"<div dir='rtl' style='color:{INK};font-size:{_pt(shape, 9.5)}'>"
                          f"{doc.note}</div>")
    if shop.thanks:
        out += _gap(7) + (f"<div align='center' style='color:{BLUE};font-weight:bold;"
                          f"font-size:{_pt(shape, 10)}'>{shop.thanks}</div>")
    if shop.show_policy and shop.policy:
        out += _gap(6) + _band(DASH) + _gap(3) + (
            f"<div dir='rtl' style='color:{MUTED};font-size:{_pt(shape, 8)}'>"
            f"{shop.policy.replace(chr(10), '<br>')}</div>")
    return out


def _contact_bar(shop, shape: Shape) -> str:
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
    if not items:
        return ""
    size = max(10, round(12 * shape.scale))
    cells = "&nbsp;&nbsp;&nbsp;&nbsp;".join(f"{_icon(name, size)}&nbsp;{text}" for name, text in items)
    return _band(INK, 1.2) + _gap(3) + (f"<div align='center' dir='rtl' style='color:{INK};"
                                        f"font-size:{_pt(shape, 8.5)}'>{cells}</div>")


def _roll(shop, doc: Document, shape: Shape) -> str:
    def rule() -> str:
        return _band(DASH)

    def row(label: str, value: str, strong: bool = False) -> str:
        weight = "font-weight:bold;" if strong else ""
        size = _pt(shape, 12) if strong else _pt(shape, 9.5)
        return (f"<table width='100%' cellspacing='0' cellpadding='0' dir='ltr'><tr>"
                f"<td align='left' dir='rtl' style='{weight}font-size:{size}'>{value}</td>"
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
        out += _gap(3) + (f"<div align='center' dir='rtl' style='color:{MUTED};"
                          f"font-size:{_pt(shape, 8)}'>{shop.policy.replace(chr(10), '<br>')}</div>")
    if shop.show_hours and shop.hours:
        out += _gap(3) + f"<div align='center' style='font-size:{_pt(shape, 8.5)}'>{shop.hours}</div>"
    return out


def body_html(shop, doc: Document, shape: Shape) -> str:
    if shape.roll:
        return _shell(_roll(shop, doc, shape) + _contact_bar(shop, shape))
    return _shell(_sheet_head(shop, doc, shape) + _info_box(doc, shape) + _lines_table(doc, shape)
                  + _totals_block(doc, shape) + _sheet_tail(shop, doc, shape))


def footer_html(shop, shape: Shape) -> str:
    """The contact bar, which a sheet pins to the foot of every page instead of letting it flow."""
    return "" if shape.roll else _shell(_contact_bar(shop, shape))


def document_html(shop, doc: Document, shape: Shape) -> str:
    if shape.roll:
        return body_html(shop, doc, shape)
    inner = _sheet_head(shop, doc, shape) + _info_box(doc, shape) + _lines_table(doc, shape) \
        + _totals_block(doc, shape) + _sheet_tail(shop, doc, shape) + _contact_bar(shop, shape)
    return _shell(inner)


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


def _load_images(text: QTextDocument, shop) -> None:
    """Qt draws an image on paper only from a resource it holds, never from a file path in the markup."""
    for name in FOOT_ICONS:
        image = icons.icon(name, MUTED, 48).pixmap(48, 48).toImage()
        text.addResource(QTextDocument.ResourceType.ImageResource, QUrl(f"{ICON_URL}{name}"), image)
    if shop.logo_path:
        text.addResource(QTextDocument.ResourceType.ImageResource, QUrl(LOGO_URL),
                         QImage(str(shop.logo_path)))


def _layout(html: str, shop, shape: Shape, width_px: float) -> QTextDocument:
    text = QTextDocument()
    _load_images(text, shop)
    text.setDefaultFont(print_font(shape))
    text.setDocumentMargin(0)
    text.setTextWidth(width_px)
    text.setHtml(html)
    return text


def render(shop, doc: Document, shape: Shape) -> QTextDocument:
    layout = page_layout(shape)
    size = layout.paintRectPixels(96).size().toSizeF()
    text = _layout(document_html(shop, doc, shape), shop, shape, size.width())
    text.setPageSize(size)
    return text


def content_px(shape: Shape) -> tuple[float, float]:
    rect = page_layout(shape).paintRectPixels(96)
    return rect.width(), rect.height()


def paint(shop, doc: Document, shape: Shape, painter: QPainter, width_px: float, height_px: float,
          new_page=None) -> int:
    """Draws the pages and pins the contact bar to the foot of each, which a flowing document cannot do."""
    foot_html = footer_html(shop, shape)
    foot = _layout(foot_html, shop, shape, width_px) if foot_html else None
    foot_h = (foot.size().height() + 8) if foot else 0
    body_h = max(60.0, height_px - foot_h)
    body = _layout(body_html(shop, doc, shape), shop, shape, width_px)
    body.setPageSize(QSizeF(width_px, body_h))
    pages = max(1, body.pageCount())
    for index in range(pages):
        if index and new_page:
            new_page()
        painter.save()
        painter.setClipRect(QRectF(0, 0, width_px, body_h))
        painter.translate(0, -index * body_h)
        body.drawContents(painter, QRectF(0, index * body_h, width_px, body_h))
        painter.restore()
        if foot:
            painter.save()
            painter.translate(0, height_px - foot.size().height())
            foot.drawContents(painter)
            painter.restore()
    return pages


def preview_image(shop, doc: Document, shape: Shape, width_px: int = 0) -> QImage:
    """Draws the paper as it prints, so the preview and the printed sheet cannot drift apart."""
    real_w, real_h = content_px(shape)
    if shape.roll:
        # A roll has no page end, so it is as long as the document
        real_h = max(1.0, _layout(body_html(shop, doc, shape), shop, shape, real_w).size().height())
    image = QImage(round(real_w), round(real_h), QImage.Format.Format_RGB32)
    image.fill(0xFFFFFFFF)
    painter = QPainter(image)
    paint(shop, doc, shape, painter, real_w, real_h)
    painter.end()
    if width_px and width_px != round(real_w):
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
    rect = printer.pageLayout().paintRectPixels(printer.resolution())
    width_px, _ = content_px(shape)
    scale = rect.width() / width_px
    painter = QPainter(printer)
    painter.scale(scale, scale)
    paint(shop, doc, shape, painter, width_px, rect.height() / scale, printer.newPage)
    painter.end()


def write_pdf(shop, doc: Document, path: str, shape_key: str = "a4") -> None:
    shape = SHAPES[shape_key]
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(path)
    to_printer(shop, doc, printer, shape)
