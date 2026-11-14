# Morv Tires

برنامج سطح مكتب لإدارة محلات الإطارات والبطاريات في مصر: البيع والشراء والمخزون بدفعات DOT،
والتقسيط، والخزينة، ومحاسبة بالقيد المزدوج. عربي بالكامل (RTL) وأرقام لاتينية.

- التقنية: Python 3.12 + PySide6-Essentials 6.12 + SQLite.
- يعمل على Windows 10/11 وLinux.
- قواعد العمل على الكود: [CLAUDE.md](CLAUDE.md).

## الحالة

| المرحلة | المحتوى | الحالة |
| --- | --- | --- |
| 0 | الأساس: الهيكل، الاختبارات الآلية، الترقيات، دليل الحسابات، محرك النسخ، هيكل الواجهة | قيد البناء |

## التشغيل للمطورين

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m unittest discover -s tests -t .
.venv/bin/python run.py
```

على Windows استبدل `.venv/bin/python` بـ `.venv\Scripts\python.exe`.

## مكان البيانات

- Windows: `%LOCALAPPDATA%\Morv Tires\`
- Linux: `~/.local/share/Morv Tires/`

يمكن تغييره للاختبار بمتغير البيئة `MORV_DATA_DIR`.
