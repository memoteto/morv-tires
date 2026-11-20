"""PDF export of a report Table through Qt's rich-text engine, RTL, A4, numbers kept left-to-right."""
from html import escape

from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument

from morv.core.money import fmt
from morv.infra.reports import CODE, DATE, MONEY, Table

_STYLE = ("body { font-size: 10pt; } th { background: #e8ecf3; } td, th { padding: 3px 6px; border: 1px solid #b8c0cc; } "
          ".total { font-weight: bold; background: #f1f3f7; } .head { font-weight: bold; }")


def table_html(table: Table) -> str:
    out = [f"<html><head><style>{_STYLE}</style></head><body dir='rtl'>",
           f"<h2>{escape(table.title)}</h2><p>{escape(table.subtitle)}</p>",
           "<table width='100%' cellspacing='0'><tr>" + "".join(f"<th>{escape(h)}</th>" for h in table.headers) + "</tr>"]
    for kind, cells in table.rows:
        css = {"head": "head", "total": "total", "grand": "total"}.get(kind, "")
        tds = []
        for value, col in zip(cells, table.kinds, strict=True):
            text = fmt(value) if col == MONEY and type(value) is int else ("" if value is None else str(value))
            tds.append(f"<td>{f'<span dir=ltr>{escape(text)}</span>' if col in (MONEY, CODE, DATE) and text else escape(text)}</td>")
        out.append(f"<tr class='{css}'>" + "".join(tds) + "</tr>")
    out.append("</table></body></html>")
    return "".join(out)


def export_pdf(table: Table, path: str) -> None:
    writer = QPdfWriter(path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageOrientation(QPageLayout.Orientation.Landscape if len(table.headers) > 4
                              else QPageLayout.Orientation.Portrait)
    doc = QTextDocument()
    doc.setHtml(table_html(table))
    doc.print_(writer)
