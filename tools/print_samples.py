"""Renders a sample of every printed document in every paper shape, as PDF and PNG, for the eye."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _shop():
    from morv.infra.shop import Shop
    return Shop(name="مورف للإطارات", phone="01000000000", address="المنصورة · شارع الجمهورية",
                tax_id="123-456-789", commercial_register="12345",
                invoice_footer="الضمان سنة على عيوب الصناعة · الاستبدال خلال 14 يومًا بالفاتورة")


def _sale():
    from morv.ui.print_doc import Column, Document
    return Document(
        title="فاتورة بيع", number="SI-000123", date="2026-11-22 18:40",
        parties=[("العميل", "أحمد محمود رمضان"), ("الموبايل", "01000000021"), ("فئة السعر", "قطاعي")],
        columns=[Column("الصنف", "40%"), Column("الكمية", "10%"), Column("السعر", "16%", money=True),
                 Column("الخصم", "14%", money=True), Column("الإجمالي", "20%", money=True)],
        rows=[("إطار Michelin Primacy 4 205/55R16 91V", "2", 645000, 0, 1290000),
              ("إطار Bridgestone Ecopia EP150 195/65R15 91H", "4", 520000, 40000, 2040000),
              ("بطارية Varta 70 أمبير", "1", 380000, 0, 380000)],
        totals=[("الإجمالي قبل الخصم", 3750000, False), ("خصم الفاتورة", 40000, False),
                ("الإجمالي النهائي", 3710000, True), ("المدفوع نقدًا", 4000000, False),
                ("الباقي للعميل", 290000, False)],
        notes="المركبة: تويوتا كورولا · لوحة ق س ط 4826 · العداد 84,500")


def _purchase():
    from morv.ui.print_doc import Column, Document
    return Document(
        title="فاتورة شراء", number="PI-000002", date="2026-11-22",
        parties=[("المورد", "الشركة المصرية للإطارات"), ("رقم فاتورة المورد", "AUTO-B9275E2345")],
        columns=[Column("الصنف", "46%"), Column("الكمية", "12%"), Column("تكلفة الوحدة", "20%", money=True),
                 Column("الإجمالي", "22%", money=True)],
        rows=[("إطار Michelin 205/55R16 91H", "50", 80000, 4000000)],
        totals=[("إجمالي الفاتورة", 4000000, True)])


def _receipt():
    from morv.ui.print_doc import Column, Document
    return Document(
        title="سند قبض", number="RC-000045", date="2026-11-22",
        parties=[("العميل", "شركة النيل للإطارات"), ("طريقة القبض", "نقدي")],
        columns=[Column("البيان", "70%"), Column("المبلغ", "30%", money=True)],
        rows=[("سداد من حساب العميل", 1500000)],
        totals=[("المبلغ المستلم", 1500000, True), ("الرصيد بعد السداد", 2300000, False)])


SAMPLES = {"sale": _sale, "purchase": _purchase, "receipt": _receipt}


def main(out_dir: str) -> int:
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QApplication
    from morv.ui import print_doc
    app = QApplication.instance() or QApplication([])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    shop = _shop()
    for name, build in SAMPLES.items():
        doc = build()
        for key, shape in print_doc.SHAPES.items():
            print_doc.write_pdf(shop, doc, str(out / f"print_{name}_{key}.pdf"), key)
            # The PNG is only so a reviewer can look; the PDF is what the shop keeps
            text = print_doc.render(shop, doc, shape)
            width_px = int((shape.width_mm - 2 * shape.margin_mm) / 25.4 * 96)
            text.setTextWidth(width_px)
            height_px = max(1, int(text.size().height()))
            image = QImage(width_px, height_px, QImage.Format.Format_RGB32)
            image.fill(0xFFFFFFFF)
            painter = QPainter(image)
            text.drawContents(painter)
            painter.end()
            image.save(str(out / f"print_{name}_{key}.png"))
            print(f"samples: {name} {key} {width_px}x{height_px}", flush=True)
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "print_out"))
