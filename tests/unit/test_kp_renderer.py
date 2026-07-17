from decimal import Decimal
from io import BytesIO
from PyPDF2 import PdfReader
from tender_sniper.kp_generator.calc import compute_totals
from tender_sniper.kp_generator.models import KPData, KPItem
from tender_sniper.kp_generator.renderer import render_kp_pdf


def _kp():
    return KPData(
        seller={"name": "ИП Хисамеева Ирина Радиевна", "inn": "7710140679",
                "kpp": "771301001", "address": "Респ. Татарстан, г. Набережные Челны",
                "phone": "+7 (915) 211-30-10", "email": "zackupkigov@yandex.ru"},
        recipient={"kuda": "", "tel": "", "komu": ""},
        number="ИПХИС-26063", vat_mode="none", delivery_time="15 к.д.",
        items=[KPItem("Бумага А4", "Бумага А4 SvetoCopy класс С", "шт.",
                      Decimal("1000"), Decimal("350"), "15 к.д.")],
        validity_days=15,
        payment_terms="100% постоплата в течение 30 рабочих дней после поставки",
        delivery_terms="DDP - доставка до склада Покупателя.",
        signer_name="Хисамеева Ирина Радиевна",
    )


def test_render_returns_pdf_bytes():
    data = render_kp_pdf(_kp())
    assert isinstance(data, bytes)
    assert data[:4] == b"%PDF"


def test_pdf_contains_number_and_total():
    data = render_kp_pdf(_kp())
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
    assert "ИПХИС-26063" in text
    assert "350 000,00" in text
    assert "Всего без НДС" in text
    assert "1000" in text
    assert "1E+3" not in text


def test_pdf_vat20_shows_itogo_and_vat_line():
    kp = KPData(
        seller={"name": "ИП Хисамеева Ирина Радиевна", "inn": "7710140679",
                "kpp": "771301001", "address": "Респ. Татарстан, г. Набережные Челны",
                "phone": "+7 (915) 211-30-10", "email": "zackupkigov@yandex.ru"},
        recipient={"kuda": "", "tel": "", "komu": ""},
        number="ИПХИС-26064", vat_mode="vat20", delivery_time="15 к.д.",
        items=[KPItem("Бумага А4", "Бумага А4 SvetoCopy класс С", "шт.",
                      Decimal("1000"), Decimal("350"), "15 к.д.")],
        validity_days=15,
        payment_terms="100% постоплата в течение 30 рабочих дней после поставки",
        delivery_terms="DDP - доставка до склада Покупателя.",
        signer_name="Хисамеева Ирина Радиевна",
    )
    totals = compute_totals(kp.items, kp.vat_mode)
    assert totals.vat_amount is not None

    data = render_kp_pdf(kp)
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
    assert "Итого" in text
    assert "в т.ч. НДС 20%" in text
