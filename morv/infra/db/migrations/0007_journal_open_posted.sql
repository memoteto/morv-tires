-- الترقية 0007: حالتا القيد (open أثناء البناء، posted بعد الاكتمال).
-- القيد لا يُرحَّل إلا مكتمل السطور ومتوازنًا؛ بعد الترحيل لا تعديل ولا سطور جديدة. (المرحلة 3)
-- الأرصدة والتقارير تقرأ القيود المرحّلة فقط؛ القيد العالق في open لا يؤثر عليها ويكشفه reconcile.

ALTER TABLE journal_entries ADD COLUMN status TEXT NOT NULL DEFAULT 'posted' CHECK (status IN ('open', 'posted'));

DROP TRIGGER journal_entries_no_update;

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
