-- الترقية 0034: أساس ضريبة القيمة المضافة (P04-4أ) — حسابان، إعدادات، ومعاملة لكل صنف. بلا تفعيل.
-- إضافية بالكامل: tax.enabled افتراضيًا '0'، فلا تتأثر أي فاتورة قائمة أو قادمة قبل تفعيل لاحق صريح.
-- 1520 تحت 15 (أصول متداولة أخرى) يوازي 1510 مصروفات مقدمة. 2350 تحت 23 (المستحقات) يوازي 2310/2320/2330/2340،
-- لا تحت 21 (الموردون) لأنها ليست التزام مورد. الترميز النهائي يحتاج اعتماد المحاسب (claude/p04-vat-design-2026-11-27.md).

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('1520', 'ضريبة القيمة المضافة - مدخلات قابلة للخصم', '15', 'asset',     'debit',  1),
  ('2350', 'ضريبة القيمة المضافة - مخرجات مستحقة',      '23', 'liability', 'credit', 1);

INSERT INTO settings (key, value) VALUES
  ('tax.enabled', '0'),
  ('tax.rate', '1400');

-- tax_treatment يحكم معاملة المخرجات عند بيع الصنف فقط؛ خصم ضريبة المدخلات مفهوم مستقل (انظر التصميم).
ALTER TABLE items ADD COLUMN tax_treatment TEXT NOT NULL DEFAULT 'standard'
  CHECK (tax_treatment IN ('standard', 'exempt', 'zero'));
ALTER TABLE items ADD COLUMN tax_rate INTEGER CHECK (tax_rate IS NULL OR tax_rate >= 0);
