-- الترقية 0004: حقول المورد الناقصة عن التصميم المعتمد (ظهرت في مراجعة المالك للمرحلة 2).
ALTER TABLE suppliers ADD COLUMN payment_days  INTEGER NOT NULL DEFAULT 0 CHECK (payment_days >= 0);
ALTER TABLE suppliers ADD COLUMN credit_limit  INTEGER NOT NULL DEFAULT 0 CHECK (credit_limit >= 0);  -- بالقرش
ALTER TABLE suppliers ADD COLUMN supplies      TEXT NOT NULL DEFAULT 'both'
                      CHECK (supplies IN ('tires', 'batteries', 'both', 'other'));
ALTER TABLE suppliers ADD COLUMN pay_method    TEXT NOT NULL DEFAULT 'cash'
                      CHECK (pay_method IN ('cash', 'transfer', 'wallet'));
ALTER TABLE suppliers ADD COLUMN commercial_reg TEXT;
ALTER TABLE suppliers ADD COLUMN bank_info      TEXT;
