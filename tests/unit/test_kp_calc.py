import pytest
from decimal import Decimal
from tender_sniper.kp_generator.models import KPItem
from tender_sniper.kp_generator.calc import format_money, parse_price, parse_qty, compute_totals


def _item(qty, price):
    return KPItem(name="Бумага А4", proposed_name="Бумага А4 SvetoCopy",
                  unit="шт.", qty=Decimal(qty), price=Decimal(price), delivery="15 к.д.")


def test_item_sum():
    assert _item(1000, "350").sum == Decimal("350000.00")


def test_format_money_russian():
    assert format_money(Decimal("3968150")) == "3 968 150,00"
    assert format_money(Decimal("350.5")) == "350,50"
    assert format_money(Decimal("0")) == "0,00"


def test_parse_price_variants():
    assert parse_price("350") == Decimal("350")
    assert parse_price("350,00") == Decimal("350.00")
    assert parse_price("1 000.50") == Decimal("1000.50")
    with pytest.raises(ValueError):
        parse_price("abc")


def test_parse_qty_positive():
    assert parse_qty("1000") == Decimal("1000")
    with pytest.raises(ValueError):
        parse_qty("0")
    with pytest.raises(ValueError):
        parse_qty("-5")


def test_compute_totals_none():
    t = compute_totals([_item(11600, "340"), _item(35, "690")], "none")
    assert t.total == Decimal("3968150.00")
    assert t.vat_amount is None


def test_compute_totals_vat20():
    t = compute_totals([_item(1000, "360")], "vat20")
    assert t.total == Decimal("360000.00")
    assert t.vat_amount == Decimal("60000.00")  # 360000 * 20 / 120
