-- الترقية 0032: فصل مبلغ تسوية المورد عن القيمة الدفترية للمخزون المستبعد عند مرتجع الشراء (P05، P07).
-- حساب 5160 يسجل الفرق المؤهل فقط بين التسوية والقيمة الدفترية؛ القيمة الدفترية والتخصيصات لا تتأثران.

INSERT INTO accounts (code, name, parent_code, kind, normal_side, postable) VALUES
  ('5160', 'فروق مرتجعات المشتريات', '51', 'expense', 'debit', 1);

ALTER TABLE purchase_returns ADD COLUMN settlement_amount INTEGER NOT NULL DEFAULT 0
  CHECK (settlement_amount >= 0);
UPDATE purchase_returns SET settlement_amount = total;
