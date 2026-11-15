-- الترقية 0013: الاعتراف الشهري بفوائد التقسيط، بالتساوي كما في جدول العقد الموقّع.
-- قيد الشهر ينقل فائدة أقساط ذلك الشهر من الفوائد المؤجلة 2410 إلى إيراد الفوائد 4140.
-- القسط الواحد لا يُعترف بفائدته مرتين؛ القيد العكسي لاحقًا إن لزم التصحيح.

CREATE TABLE interest_recognitions (
  id               INTEGER PRIMARY KEY,
  number           TEXT NOT NULL UNIQUE,
  period           TEXT NOT NULL UNIQUE CHECK (period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
  entry_date       TEXT NOT NULL CHECK (entry_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  amount           INTEGER NOT NULL CHECK (amount > 0),
  journal_entry_id INTEGER NOT NULL UNIQUE REFERENCES journal_entries(id),
  user_id          INTEGER REFERENCES users(id),
  at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime'))
);

CREATE TABLE interest_recognition_rows (
  id             INTEGER PRIMARY KEY,
  recognition_id INTEGER NOT NULL REFERENCES interest_recognitions(id),
  row_id         INTEGER NOT NULL UNIQUE REFERENCES installment_rows(id),
  amount         INTEGER NOT NULL CHECK (amount > 0)
);
CREATE INDEX interest_recognition_rows_recognition ON interest_recognition_rows(recognition_id);

CREATE TRIGGER interest_recognitions_no_update BEFORE UPDATE ON interest_recognitions BEGIN SELECT RAISE(ABORT, 'قيد فوائد مرحّل: لا تعديل'); END;
CREATE TRIGGER interest_recognitions_no_delete BEFORE DELETE ON interest_recognitions BEGIN SELECT RAISE(ABORT, 'قيد فوائد مرحّل: لا حذف'); END;
CREATE TRIGGER interest_recognition_rows_no_update BEFORE UPDATE ON interest_recognition_rows BEGIN SELECT RAISE(ABORT, 'سطر اعتراف مرحّل: لا تعديل'); END;
CREATE TRIGGER interest_recognition_rows_no_delete BEFORE DELETE ON interest_recognition_rows BEGIN SELECT RAISE(ABORT, 'سطر اعتراف مرحّل: لا حذف'); END;
