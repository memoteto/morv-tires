-- الترقية 0033: توزيع الشحن وخصومات الصنف والفاتورة على فاتورة الشراء (P01، P02).
-- إضافية بالكامل: القيم الافتراضية تُبقي الفواتير القديمة كما هي (شحن صفر، خصم صفر، أساس كمية).
-- net_total هو القيمة الدفترية الصافية للسطر بعد الخصومات وإضافة نصيب الشحن؛ هو ما يغذي cost_layers.cost_total؛
-- unit_cost وline_total يبقيان سعر الفاتورة التاريخي دون تعديل، للمقارنة كما يلزم P02.
-- تسمية الأعمدة تطابق sales/sale_lines (line_discount، invoice_share) لنفس المفهوم.

ALTER TABLE purchase_invoices ADD COLUMN freight_amount INTEGER NOT NULL DEFAULT 0 CHECK (freight_amount >= 0);
ALTER TABLE purchase_invoices ADD COLUMN freight_basis TEXT NOT NULL DEFAULT 'qty' CHECK (freight_basis IN ('qty', 'value', 'manual'));
ALTER TABLE purchase_invoices ADD COLUMN invoice_discount INTEGER NOT NULL DEFAULT 0 CHECK (invoice_discount >= 0);

ALTER TABLE purchase_lines ADD COLUMN line_discount INTEGER NOT NULL DEFAULT 0 CHECK (line_discount >= 0);
ALTER TABLE purchase_lines ADD COLUMN invoice_share INTEGER NOT NULL DEFAULT 0 CHECK (invoice_share >= 0);
ALTER TABLE purchase_lines ADD COLUMN freight_share INTEGER NOT NULL DEFAULT 0 CHECK (freight_share >= 0);
ALTER TABLE purchase_lines ADD COLUMN net_total INTEGER NOT NULL DEFAULT 0 CHECK (net_total >= 0);

-- backfill مرحلي فقط؛ يُعاد إنشاء قيد منع التعديل فورًا بنفس تعريفه الأصلي من 0006.
DROP TRIGGER purchase_lines_no_update;
UPDATE purchase_lines SET net_total = line_total;
CREATE TRIGGER purchase_lines_no_update BEFORE UPDATE ON purchase_lines BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
