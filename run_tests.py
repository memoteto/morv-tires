"""تشغيل كل الاختبارات ثم إنهاء العملية فورًا بنتيجتها.

لماذا: بعد نجاح الاختبارات كلها كان المفسّر ينهار (رمز 139) على Linux أثناء تدمير كائنات Qt
بترتيب غير مضمون عند الخروج. النتيجة التي تهمنا هي نتيجة الاختبارات، فنطبعها ونخرج بـ os._exit
قبل مرحلة التدمير تلك.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0 if result.wasSuccessful() else 1)
