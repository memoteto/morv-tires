-- الترقية 0035: ضريبة مدخلات على فاتورة الشراء (P04-4ب)، تبني على 0033 (الشحن والخصومات) و0034 (الأساس).
-- إضافية بالكامل: الافتراضي صفر/0 (غير شامل، شحن غير خاضع)؛ لا يتغير سلوك أي فاتورة قديمة أو جديدة
-- قبل تفعيل tax.enabled صراحة. tax_base/tax_rate/tax_amount تُحسب لكل سطر بـ morv/core/tax.py
-- (purchase_line_split) وتوثّق الحساب، لا تُستخدم لإعادة حسابه. tax_total على رأس الفاتورة = مجموع
-- tax_amount لكل الأسطر. يُقيَّد على 1520 مجموع deductible_tax_amount فقط؛ الباقي ضمن تكلفة الاقتناء.
-- لا backfill: الافتراضات صحيحة لكل الصفوف القائمة (ضريبة معطّلة حتى الآن).

ALTER TABLE purchase_invoices ADD COLUMN prices_tax_inclusive INTEGER NOT NULL DEFAULT 0 CHECK (prices_tax_inclusive IN (0, 1));
ALTER TABLE purchase_invoices ADD COLUMN freight_taxable INTEGER NOT NULL DEFAULT 0 CHECK (freight_taxable IN (0, 1));
ALTER TABLE purchase_invoices ADD COLUMN tax_total INTEGER NOT NULL DEFAULT 0 CHECK (tax_total >= 0);

ALTER TABLE purchase_lines ADD COLUMN tax_base INTEGER NOT NULL DEFAULT 0 CHECK (tax_base >= 0);
ALTER TABLE purchase_lines ADD COLUMN tax_rate INTEGER NOT NULL DEFAULT 0 CHECK (tax_rate >= 0);
ALTER TABLE purchase_lines ADD COLUMN tax_amount INTEGER NOT NULL DEFAULT 0 CHECK (tax_amount >= 0);
ALTER TABLE purchase_lines ADD COLUMN deductible_tax_amount INTEGER NOT NULL DEFAULT 0
  CHECK (deductible_tax_amount IN (0, tax_amount));
