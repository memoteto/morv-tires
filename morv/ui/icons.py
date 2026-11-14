"""أيقونات القائمة الجانبية: رسومات SVG بسيطة مكتوبة هنا، تُلوَّن حسب الثيم.

لا تُستخدم ملفات أيقونات خارجية، حتى لا يحتاج البرنامج ملفات إضافية أو تراخيص.
"""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# مسارات بأسلوب الخط (stroke) داخل مربع 24×24
PATHS = {
    "dashboard": "<rect x='3' y='3' width='7' height='9' rx='1'/><rect x='14' y='3' width='7' height='5' rx='1'/><rect x='14' y='12' width='7' height='9' rx='1'/><rect x='3' y='16' width='7' height='5' rx='1'/>",
    "sale": "<circle cx='9' cy='20' r='1.5'/><circle cx='18' cy='20' r='1.5'/><path d='M2 3h3l2.5 12h11.5l2-8H6.2'/>",
    "invoices": "<path d='M6 2h9l4 4v16H6z'/><path d='M14 2v5h5M9 12h7M9 16h7'/>",
    "purchase": "<path d='M3 7h13v10H3zM16 10h3l2 3v4h-5'/><circle cx='7' cy='18' r='1.5'/><circle cx='18' cy='18' r='1.5'/>",
    "items": "<circle cx='12' cy='12' r='9'/><circle cx='12' cy='12' r='3'/><path d='M12 3v6M12 15v6M3 12h6M15 12h6'/>",
    "customers": "<circle cx='9' cy='8' r='3.5'/><path d='M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6'/><path d='M16 4.5a3.5 3.5 0 0 1 0 7M18 14c2.2.6 3.5 2.8 3.5 6'/>",
    "suppliers": "<path d='M3 21V9l9-6 9 6v12'/><path d='M9 21v-7h6v7'/>",
    "installments": "<rect x='3' y='5' width='18' height='16' rx='2'/><path d='M3 10h18M8 3v4M16 3v4M8 14h3M8 17h6'/>",
    "treasury": "<rect x='2' y='6' width='20' height='13' rx='2'/><circle cx='12' cy='12.5' r='3'/><path d='M6 9.5v6M18 9.5v6'/>",
    "reports": "<path d='M4 20V10M10 20V4M16 20v-7M22 20H2'/>",
    "settings": "<circle cx='12' cy='12' r='3'/><path d='M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1'/>",
    "backup": "<ellipse cx='12' cy='5' rx='8' ry='3'/><path d='M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3'/>",
    "collapse": "<path d='M13 6l6 6-6 6M5 6l6 6-6 6'/>",
    "expand": "<path d='M11 6l-6 6 6 6M19 6l-6 6 6 6'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'/>",
    "moon": "<path d='M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z'/>",
}


def icon(name: str, color: str, size: int = 20) -> QIcon:
    """أيقونة بلون محدد. ترسم SVG على صورة شفافة بحجم مضاعف لتكون حادة على الشاشات عالية الدقة."""
    svg = (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='{color}' "
           f"stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'>{PATHS[name]}</svg>")
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size * 2, size * 2)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    renderer.render(painter, QRectF(0, 0, size * 2, size * 2))
    painter.end()
    pix.setDevicePixelRatio(2.0)
    return QIcon(pix)
