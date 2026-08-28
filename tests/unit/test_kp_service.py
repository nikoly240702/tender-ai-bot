from decimal import Decimal
from tender_sniper.kp_generator.models import KPItem
from tender_sniper.kp_generator.service import build_kp_data


def test_build_kp_data_maps_profile():
    profile = {
        "company_name": "ИП Хисамеева Ирина Радиевна", "inn": "7710140679",
        "kpp": "771301001", "legal_address": "Респ. Татарстан",
        "phone": "+7 (915) 211-30-10", "email": "z@ya.ru",
        "kp_default_validity_days": 15,
        "kp_default_payment_terms": "100% постоплата",
        "kp_default_delivery_terms": "DDP", "kp_signer_name": "Хисамеева И.Р.",
    }
    kp = build_kp_data(profile, {"kuda": "", "tel": "", "komu": ""},
                       "none", "15 к.д.", "ИПХИС-26064",
                       [KPItem("Бумага", "Бумага А4", "шт.", Decimal("10"), Decimal("350"), "15 к.д.")])
    assert kp.seller["name"] == "ИП Хисамеева Ирина Радиевна"
    assert kp.seller["inn"] == "7710140679"
    assert kp.number == "ИПХИС-26064"
    assert kp.validity_days == 15
    assert kp.signer_name == "Хисамеева И.Р."
    assert len(kp.items) == 1
