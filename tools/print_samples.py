"""Renders a sample of every printed document in every paper shape, as PDF and PNG, for the eye."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from morv.core.money import fmt  # noqa: E402
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _shop():
    from morv.infra.shop import Shop
    return Shop(name="مورف للإطارات", activity="إطارات · بطاريات · خدمات سريعة",
                phone="01000000000", phone2="01222222222", whatsapp="01000000000",
                address="المنصورة · شارع الجمهورية · أمام محطة مصر",
                hours="السبت - الخميس · 9 ص إلى 10 م", tax_registered=True,
                tax_id="123-456-789", commercial_register="12345",
                thanks="شكرًا لتعاملكم معنا",
                policy="الاستبدال خلال 14 يومًا بالفاتورة وبحالتها الأصلية.\nالضمان لا يغطي سوء الاستخدام أو الحفر.")


def _sale():
    from morv.ui.print_doc import sample_document
    return sample_document()


def _purchase():
    from morv.ui.print_doc import Document, Line
    return Document(
        title="فاتورة شراء", number="P-2026-000002", date="2026-11-22",
        info=[("المورد", "الشركة المصرية للإطارات"), ("فاتورة المورد", "AUTO-B9275E2345"),
              ("الدفع", "على الحساب")],
        lines=[Line("إطار ميشلان Primacy 4", "205/55R16 · 91H", "50", 80000, 4000000)],
        totals=[("الإجمالي", f"{fmt(4000000)} ج.م")])


def _receipt():
    from morv.ui.print_doc import Document
    return Document(
        title="سند قبض", number="RC-2026-000045", date="2026-11-22",
        info=[("العميل", "شركة النيل للإطارات"), ("طريقة القبض", "نقدي")],
        totals=[("المبلغ المستلم", f"{fmt(1500000)} ج.م"), ("الرصيد بعد السداد", fmt(2300000))],
        note="وصل بمبلغ ألف وخمسمائة جنيه سدادًا من حساب العميل.")


def _vat_sale(inclusive: bool):
    from morv.infra.sale_view import SaleLineView, SaleView
    from morv.ui.printing import sale_document

    # These sample snapshots are consistent with the actual engine's 14%/exempt
    # calculations for 1000.00 + 1000.00 after 200.00 invoice discount.
    tax = 11053 if inclusive else 12600
    payable = 90000 if inclusive else 102600
    total = payable + 90000
    sale = SaleView(
        1, "SI-000001", "عميل نقدي", "2026-11-28", 200000, 0, 20000, total, None, "",
        (("cash", total, total, None),),
        (SaleLineView(1, "خدمة تركيب", 1, 100000, 0, 10000, payable,
                      "standard", 1400, tax, inclusive),
         SaleLineView(2, "خدمة معفاة", 1, 100000, 0, 10000, 90000,
                      "exempt", 0, 0, inclusive)),
        0, (), True, inclusive, tax,
        customer_mobile="01000000021", customer_address="المنصورة",
        cashier_name="المدير", recorded_at="2026-11-28 08:00:00",
        notes="مثال تجريبي لطباعة فاتورة ضريبية")
    return sale_document(sale, _shop())


SAMPLES = {"sale": _sale, "purchase": _purchase, "receipt": _receipt,
           "sale_vat_inclusive": lambda: _vat_sale(True),
           "sale_vat_exclusive": lambda: _vat_sale(False)}


def main(out_dir: str) -> int:
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
            image = print_doc.preview_image(shop, doc, shape)
            image.save(str(out / f"print_{name}_{key}.png"))
            print(f"samples: {name} {key} {image.width()}x{image.height()}", flush=True)
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "print_out"))
