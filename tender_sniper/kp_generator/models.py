from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass
class KPItem:
    name: str
    proposed_name: str
    unit: str
    qty: Decimal
    price: Decimal
    delivery: str = ""

    @property
    def sum(self) -> Decimal:
        return _q2(self.qty * self.price)


@dataclass
class Totals:
    total: Decimal
    vat_amount: Optional[Decimal] = None


@dataclass
class KPData:
    seller: dict
    recipient: dict
    number: str
    vat_mode: str            # 'none' | 'vat20'
    delivery_time: str
    items: list
    validity_days: int = 15
    payment_terms: str = ""
    delivery_terms: str = ""
    signer_name: str = ""
