"""حسابات FIFO النقية: شرائح التكلفة بالقرش، وخطة صرف الدفعات الفعلية. بلا قاعدة بيانات."""


class InsufficientStock(ValueError):
    """الكمية المطلوبة أكبر من المتاح."""


def slice_cost(cost_remaining: int, qty_remaining: int, take: int) -> int:
    """تكلفة take وحدة من طبقة متبقٍ فيها qty_remaining بقيمة cost_remaining؛ آخر وحدة تأخذ الباقي كاملًا."""
    if not (0 < take <= qty_remaining) or cost_remaining < 0:
        raise ValueError("كمية أو تكلفة غير صالحة")
    if take == qty_remaining:
        return cost_remaining
    # تقريب نصف لأعلى بالأعداد الصحيحة فقط، فلا يتراكم فرق قروش
    return (2 * cost_remaining * take + qty_remaining) // (2 * qty_remaining)


def plan_fifo(layers, qty: int) -> list:
    """طبقات (id, كمية متبقية, تكلفة متبقية) من الأقدم؛ يعيد [(id, كمية, تكلفة)] لصرف qty."""
    if qty <= 0:
        raise ValueError("الكمية يجب أن تكون موجبة")
    out, need = [], qty
    for layer_id, q_rem, c_rem in layers:
        if need == 0:
            break
        take = min(need, q_rem)
        if take > 0:
            out.append((layer_id, take, slice_cost(c_rem, q_rem, take)))
            need -= take
    if need:
        raise InsufficientStock(f"الرصيد غير كافٍ: ينقص {need}")
    return out


def plan_lots(lots, qty: int) -> dict:
    """دفعات (id, رصيد) مرتبة بالأقدم تصنيعًا أولًا؛ يعيد {id: كمية} المقترح صرفها."""
    out, need = {}, qty
    for lot_id, on_hand in lots:
        if need == 0:
            break
        take = min(need, on_hand)
        if take > 0:
            out[lot_id] = take
            need -= take
    if need:
        raise InsufficientStock(f"الرصيد غير كافٍ: ينقص {need}")
    return out
