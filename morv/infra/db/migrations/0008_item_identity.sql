-- الترقية 0008: هوية الصنف في الدفعة وطبقة التكلفة يجب أن تطابق سطر الشراء الذي نشأتا منه. (المرحلة 3)
-- التعديل اللاحق للصنف محمي أصلًا بمحفّزات 0006؛ هذه تحمي لحظة الإدراج.

CREATE TRIGGER stock_lots_item_matches_line BEFORE INSERT ON stock_lots
WHEN NEW.item_id IS NOT (SELECT item_id FROM purchase_lines WHERE id = NEW.purchase_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الدفعة لا تطابق سطر الشراء'); END;

CREATE TRIGGER cost_layers_item_matches_line BEFORE INSERT ON cost_layers
WHEN NEW.item_id IS NOT (SELECT item_id FROM purchase_lines WHERE id = NEW.purchase_line_id)
BEGIN SELECT RAISE(ABORT, 'هوية الصنف في الطبقة لا تطابق سطر الشراء'); END;
