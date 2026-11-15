"""Pure FIFO costing: cost slices in piasters and lot issue plans. No database."""


class InsufficientStock(ValueError):
    """Asked for more than the stock holds."""


def slice_cost(cost_remaining: int, qty_remaining: int, take: int) -> int:
    if not (0 < take <= qty_remaining) or cost_remaining < 0:
        raise ValueError("كمية أو تكلفة غير صالحة")
    if take == qty_remaining:
        return cost_remaining
    # Half-up rounding in integers only, so piaster drift cannot build up
    return (2 * cost_remaining * take + qty_remaining) // (2 * qty_remaining)


def plan_fifo(layers, qty: int) -> list:
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


def take_units(parts, done: int, qty: int) -> list:
    if qty <= 0 or done < 0:
        raise ValueError("الكمية موجبة والمقتطع سابقًا غير سالب")
    if done + qty > sum(q for _, q, _ in parts):
        raise InsufficientStock("المطلوب أكبر مما صُرف فعلًا")
    out, start = [], 0
    for key, q, value in parts:
        lo, hi = max(done, start), min(done + qty, start + q)
        if hi > lo:
            a, b = lo - start, hi - start
            out.append((key, hi - lo, value * b // q - value * a // q))
        start += q
    return out


def plan_lots(lots, qty: int) -> dict:
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
