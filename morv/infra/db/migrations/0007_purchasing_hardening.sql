-- الترقية 0007: تحصين المشتريات فوق 0006 (المرحلة 3). جسر من مخطط 0006 كما طُبّق على القواعد القائمة.
-- لا يُعدَّل 0006 أبدًا بعد تطبيقه؛ كل ما يلي إضافة عليه، مع تعبئة أي صفوف قائمة.

-- 1) القيد: عدد سطور وإجمالي معلنان، وحالة open أثناء البناء ثم posted.
--    القيم الافتراضية للأعمدة الجديدة لازمة لـ ALTER فقط؛ الكود يمرّر القيم صراحةً دائمًا.
ALTER TABLE journal_entries ADD COLUMN line_count INTEGER NOT NULL DEFAULT 2 CHECK (line_count >= 2);
ALTER TABLE journal_entries ADD COLUMN total INTEGER NOT NULL DEFAULT 1 CHECK (total > 0);
ALTER TABLE journal_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('open', 'posted'));

DROP TRIGGER journal_entries_no_update;
UPDATE journal_entries SET
  line_count = max(2, (SELECT count(*) FROM journal_lines WHERE entry_id = journal_entries.id)),
  total      = max(1, (SELECT coalesce(sum(debit), 0) FROM journal_lines WHERE entry_id = journal_entries.id));

-- بعد اكتمال عدد السطور لا يُقبل سطر إضافي، ومع آخر سطر يجب أن يتساوى المدين والدائن مع الإجمالي المعلن
CREATE TRIGGER journal_lines_sealed BEFORE INSERT ON journal_lines
WHEN (SELECT count(*) FROM journal_lines WHERE entry_id = NEW.entry_id)
     >= (SELECT line_count FROM journal_entries WHERE id = NEW.entry_id)
BEGIN SELECT RAISE(ABORT, 'القيد مكتمل: لا تُضاف سطور بعد ترحيله'); END;
CREATE TRIGGER journal_lines_balanced AFTER INSERT ON journal_lines
WHEN (SELECT count(*) FROM journal_lines WHERE entry_id = NEW.entry_id)
     = (SELECT line_count FROM journal_entries WHERE id = NEW.entry_id)
 AND ((SELECT sum(debit) FROM journal_lines WHERE entry_id = NEW.entry_id)
        <> (SELECT total FROM journal_entries WHERE id = NEW.entry_id)
   OR (SELECT sum(credit) FROM journal_lines WHERE entry_id = NEW.entry_id)
        <> (SELECT total FROM journal_entries WHERE id = NEW.entry_id))
BEGIN SELECT RAISE(ABORT, 'القيد غير متوازن'); END;

-- الرأس يُنشأ دائمًا مفتوحًا
CREATE TRIGGER journal_entries_new_open BEFORE INSERT ON journal_entries WHEN NEW.status <> 'open'
BEGIN SELECT RAISE(ABORT, 'القيد يبدأ مفتوحًا ثم يُرحَّل'); END;

-- التعديل الوحيد المسموح: open ← posted بلا تغيير في أي عمود آخر
CREATE TRIGGER journal_entries_only_post BEFORE UPDATE ON journal_entries
WHEN NOT (OLD.status = 'open' AND NEW.status = 'posted' AND OLD.id = NEW.id AND OLD.entry_date = NEW.entry_date
          AND OLD.source_type = NEW.source_type AND OLD.source_id = NEW.source_id AND OLD.memo IS NEW.memo
          AND OLD.line_count = NEW.line_count AND OLD.total = NEW.total AND OLD.user_id IS NEW.user_id
          AND OLD.posted_at = NEW.posted_at)
BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;

-- لا يُرحَّل القيد إلا إذا اكتمل عدد سطوره وتساوى المدين والدائن مع إجماليه المعلن
CREATE TRIGGER journal_entries_post_complete BEFORE UPDATE ON journal_entries
WHEN OLD.status = 'open' AND NEW.status = 'posted'
 AND ((SELECT count(*) FROM journal_lines WHERE entry_id = OLD.id) <> OLD.line_count
   OR (SELECT coalesce(sum(debit), 0) FROM journal_lines WHERE entry_id = OLD.id) <> OLD.total
   OR (SELECT coalesce(sum(credit), 0) FROM journal_lines WHERE entry_id = OLD.id) <> OLD.total)
BEGIN SELECT RAISE(ABORT, 'القيد ناقص أو غير متوازن: لا يُرحَّل'); END;

-- السطور تُضاف إلى قيد مفتوح فقط
CREATE TRIGGER journal_lines_only_open BEFORE INSERT ON journal_lines
WHEN (SELECT status FROM journal_entries WHERE id = NEW.entry_id) = 'posted'
BEGIN SELECT RAISE(ABORT, 'القيد مرحّل: لا تُضاف سطور جديدة'); END;

-- 2) الصرف: مرجع سطر المصدر فريد (سطر المستند يُصرف مرة واحدة). الصفوف القائمة تأخذ رقم الصرف نفسه.
ALTER TABLE stock_issues ADD COLUMN source_line_id INTEGER NOT NULL DEFAULT 0;
DROP TRIGGER stock_issues_no_update;
UPDATE stock_issues SET source_line_id = id;
CREATE TRIGGER stock_issues_no_update BEFORE UPDATE ON stock_issues BEGIN SELECT RAISE(ABORT, 'مستند مرحّل: التعديل ممنوع'); END;
CREATE UNIQUE INDEX stock_issues_source_unique ON stock_issues(source_type, source_id, source_line_id);
