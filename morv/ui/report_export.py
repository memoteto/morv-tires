"""PDF export of a report Table through Qt's rich-text engine, RTL, A4, numbers kept left-to-right."""
import re
from datetime import date
from html import escape

from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

from morv.core.money import fmt
from morv.infra.reports import CODE, DATE, MONEY, Table
from morv.ui.tokens import LIGHT, REPORT_ROW_BACKGROUND

_STYLE = (f"body {{ font-size: 10pt; color: {LIGHT['text']}; }} "
          f"th {{ background: {LIGHT['border']}; }} "
          f"td, th {{ padding: 3px 6px; border: 1px solid {LIGHT['border_strong']}; }} "
          + " ".join(f".{kind} {{ font-weight: bold; background: {LIGHT[key]}; }}"
                     for kind, key in REPORT_ROW_BACKGROUND.items())
          + f" .grand {{ color: {LIGHT['total_text']}; }}")
_NUMBER = re.compile(r"([+-]?\d[\d,.\-:/]*)")


def _ltr(text: str) -> str:
    # Qt rich text ignores the direction on inline spans for some grouped values.
    return f"<span dir='ltr'>\u202a{escape(text)}\u202c</span>"


def _rich(text: str) -> str:
    """Escaped text with every date and number kept left-to-right inside the RTL line."""
    return "".join(_ltr(part) if i % 2 else escape(part) for i, part in enumerate(_NUMBER.split(text)))


def table_html(table: Table, *, shop_name: str = "", printed_on: str = "") -> str:
    out = [f"<html><head><style>{_STYLE}</style></head><body dir='rtl'>",
           f"<p style='font-size:16pt; font-weight:bold'>{_rich(table.title)}</p><p>{_rich(table.subtitle)}</p>"]
    metadata = [part for part in (shop_name, f"تاريخ الطباعة: {printed_on}" if printed_on else "") if part]
    if metadata:
        out.append(f"<p>{_rich(' · '.join(metadata))}</p>")
    # QTextDocument places physical table columns left to right even under an RTL body.
    out.append("<table dir='ltr' width='100%' cellspacing='0'><tr>" +
               "".join(f"<th dir='rtl'>{_rich(h)}</th>" for h in reversed(table.headers)) + "</tr>")
    for kind, cells in table.rows:
        css = kind if kind in REPORT_ROW_BACKGROUND else ""
        tds = []
        for value, col in zip(reversed(cells), reversed(table.kinds), strict=True):
            text = fmt(value) if col == MONEY and type(value) is int else ("" if value is None else str(value))
            if col in (MONEY, CODE, DATE):
                tds.append(f"<td dir='ltr' align='left'>{_ltr(text) if text else ''}</td>")
            else:
                tds.append(f"<td dir='rtl' align='right'>{_rich(text)}</td>")
        out.append(f"<tr class='{css}'>" + "".join(tds) + "</tr>")
    out.append("</table></body></html>")
    return "".join(out)


def export_pdf(table: Table, path: str, *, shop_name: str = "") -> None:
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageOrientation(QPageLayout.Orientation.Landscape if len(table.headers) > 4
                              else QPageLayout.Orientation.Portrait)
    doc = QTextDocument()
    doc.setHtml(table_html(table, shop_name=shop_name, printed_on=date.today().isoformat()))
    doc.print_(writer)
