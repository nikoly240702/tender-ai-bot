from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from .models import KPItem, Totals


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value: Decimal) -> str:
    """Русский формат: '3 968 150,00' (пробел-тысячи, запятая-дроби, 2 знака)."""
    v = _q2(Decimal(value))
    sign = "-" if v < 0 else ""
    v = abs(v)
    int_part, frac_part = f"{v:.2f}".split(".")
    groups = []
    while len(int_part) > 3:
        groups.insert(0, int_part[-3:])
        int_part = int_part[:-3]
    groups.insert(0, int_part)
    return f"{sign}{' '.join(groups)},{frac_part}"


def parse_price(s: str) -> Decimal:
    cleaned = (s or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        v = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Не число: {s!r}")
    if v < 0:
        raise ValueError("Цена не может быть отрицательной")
    return v


def parse_qty(s: str) -> Decimal:
    cleaned = (s or "").strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        v = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        raise ValueError(f"Не число: {s!r}")
    if v <= 0:
        raise ValueError("Кол-во должно быть больше нуля")
    return v


def compute_totals(items, vat_mode: str) -> Totals:
    total = _q2(sum((it.sum for it in items), Decimal("0")))
    if vat_mode == "vat20":
        vat = _q2(total * Decimal("20") / Decimal("120"))
        return Totals(total=total, vat_amount=vat)
    return Totals(total=total, vat_amount=None)
