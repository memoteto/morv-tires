-- الترقية 0005: علامة صريحة لاكتمال الإعداد الأول (setup.completed_at).
-- قبلها كان البرنامج يعتبر الإعداد مكتملًا بمجرد وجود مستخدم، فإذا انقطع الإعداد بعد إنشاء المدير
-- وقبل إعداد التشفير بقيت القاعدة نصف مُعدّة ولا يظهر المعالج ثانية لإكمالها.
-- القواعد القائمة التي فيها مستخدم وحلقة مفاتيح تُعتبر مكتملة؛ وما عداها يُكمل المعالج إعداده.
INSERT INTO settings (key, value)
SELECT 'setup.completed_at', strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')
WHERE EXISTS (SELECT 1 FROM users)
  AND EXISTS (SELECT 1 FROM settings WHERE key = 'backup.keyring' AND value <> '');
