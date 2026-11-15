"""Sidebar icons: small inline SVG drawings, tinted by theme."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# Stroke paths inside a 24x24 box
PATHS = {
    "home": "<path d='M3 11l9-8 9 8'/><path d='M5 9.5V21h5v-6h4v6h5V9.5'/>",
    "pos": "<circle cx='9' cy='20' r='1.5'/><circle cx='18' cy='20' r='1.5'/><path d='M2 3h3l2.5 12h11.5l2-8H6.2'/>",
    "sales": "<path d='M6 2h9l4 4v16H6z'/><path d='M14 2v5h5M9 12h7M9 16h7'/>",
    "purchases": "<path d='M21 8l-9-5-9 5v8l9 5 9-5z'/><path d='M3 8l9 5 9-5M12 13v8'/>",
    "inventory": "<circle cx='12' cy='12' r='9'/><circle cx='12' cy='12' r='3'/><path d='M12 3v6M12 15v6M3 12h6M15 12h6'/>",
    "customers": "<circle cx='9' cy='8' r='3.5'/><path d='M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6'/><path d='M16 4.5a3.5 3.5 0 0 1 0 7M18 14c2.2.6 3.5 2.8 3.5 6'/>",
    # A delivery truck: the old building icon read as nothing in particular
    "suppliers": "<path d='M3 7h13v10H3zM16 10h3l2 3v4h-5'/><circle cx='7' cy='18' r='1.5'/><circle cx='18' cy='18' r='1.5'/>",
    "installments": "<rect x='3' y='5' width='18' height='16' rx='2'/><path d='M3 10h18M8 3v4M16 3v4M8 14h3M8 17h6'/>",
    "treasury": "<rect x='2' y='6' width='20' height='13' rx='2'/><circle cx='12' cy='12.5' r='3'/><path d='M6 9.5v6M18 9.5v6'/>",
    "reports": "<path d='M4 20V10M10 20V4M16 20v-7M22 20H2'/>",
    # A toothed cog, so it cannot be mistaken for the light-mode sun
    "settings": "<circle cx='12' cy='12' r='3'/><path d='M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z'/>",
    "collapse": "<path d='M13 6l6 6-6 6M5 6l6 6-6 6'/>",
    "expand": "<path d='M11 6l-6 6 6 6M19 6l-6 6 6 6'/>",
    "sun": "<circle cx='12' cy='12' r='4'/><path d='M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4'/>",
    "eye": "<path d='M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z'/><circle cx='12' cy='12' r='3'/>",
    "eye_off": "<path d='M3 3l18 18M10.6 10.6a3 3 0 0 0 4.2 4.2M9.9 5.2A10.5 10.5 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.2 6.2A16.8 16.8 0 0 0 2 12s3.5 7 10 7c1.6 0 3-.4 4.3-1'/>",
    "alert": "<path d='M12 3 2 20h20z'/><path d='M12 10v4M12 17h.01'/>",
    "check": "<path d='m5 12 5 5 9-10'/>",
    "logout": "<path d='M9 7l-5 5 5 5M4 12h11M20 4v16'/>",
    "usb": "<path d='M8 3h8v8H8z'/><path d='M6 11h12v8a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2z'/>",
    "shield": "<path d='M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6z'/><path d='m9 12 2 2 4-4'/>",
    "plus": "<path d='M12 5v14M5 12h14'/>",
    "edit": "<path d='M4 20h4L19 9l-4-4L4 16z'/>",
    "pause": "<circle cx='12' cy='12' r='9'/><path d='M10 9v6M14 9v6'/>",
    "play": "<circle cx='12' cy='12' r='9'/><path d='M10 8l6 4-6 4z'/>",
    "save": "<path d='M5 3h11l3 3v15H5z'/><path d='M8 3v5h7V3M8 21v-7h8v7'/>",
    "search": "<circle cx='11' cy='11' r='7'/><path d='m20 20-3.5-3.5'/>",
    "tire": "<circle cx='12' cy='12' r='9'/><circle cx='12' cy='12' r='4'/><path d='M12 3v5M12 16v5M3 12h5M16 12h5'/>",
    "battery": "<rect x='3' y='7' width='18' height='13' rx='2'/><path d='M7 7V5h3v2M14 7V5h3v2M6.5 13.5h3M15.5 12v3M14 13.5h3'/>",
    "service": "<circle cx='12' cy='13' r='8'/><path d='m12 13 4-4M9 3h6'/>",
    "user": "<circle cx='12' cy='8' r='4'/><path d='M4 21c0-4 3.6-7 8-7s8 3 8 7'/>",
    "truck": "<path d='M3 7h13v10H3zM16 10h3l2 3v4h-5'/><circle cx='7' cy='18' r='1.5'/><circle cx='18' cy='18' r='1.5'/>",
    "font": "<path d='M4 20 11 4h2l7 16M7 14h10'/>",
    "close": "<path d='M6 6l12 12M18 6 6 18'/>",
    "moon": "<path d='M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z'/>",
}


def icon(name: str, color: str, size: int = 20) -> QIcon:
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
