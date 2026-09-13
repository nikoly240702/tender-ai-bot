import pytest
from tender_sniper.sources.mos_portal_mapper import ks_dto_to_tender

SAMPLE_DTO = {
    "id": 4271956,
    "name": "Поставка бумаги офисной А4",
    "status": "Active",
    # Реальный API отдаёт company как объект {inn, name, id}, не строку —
    # подтверждено живым ответом 13.09.2026. Изначальное предположение (строка)
    # приводило к 'dict' object has no attribute 'lower' в SmartMatcher, то
    # есть НИ ОДИН тендер с этого источника не мог реально матчиться.
    "company": {"inn": "7707090925", "name": 'ГБУ "Жилищник района Марьино"', "id": 1150496},
    "federalLaw": None,
    "conclusionReason": None,
    "startPrice": 185000.0,
    "beginDate": "2026-09-10T09:00:00",
    "endDate": "2026-09-15T18:00:00",
}


@pytest.mark.unit
class TestKsDtoToTender:
    def test_maps_all_fields(self):
        t = ks_dto_to_tender(SAMPLE_DTO)
        assert t["number"] == "MOS-4271956"
        assert t["name"] == "Поставка бумаги офисной А4"
        assert t["description"] == ""
        assert t["price"] == 185000.0
        assert t["region"] == "Москва"
        assert t["customer_name"] == 'ГБУ "Жилищник района Марьино"'
        assert isinstance(t["customer_name"], str)
        assert t["published_date"] == "2026-09-10T09:00:00"
        assert t["submission_deadline"] == "2026-09-15T18:00:00"
        assert t["source_label"] == "Портал поставщиков (Москва)"
        assert "zakupki.mos.ru" in t["url"]
        assert "4271956" in t["url"]

    def test_number_never_collides_with_zakupki_gov_ru_format(self):
        t = ks_dto_to_tender(SAMPLE_DTO)
        # zakupki.gov.ru numbers are all-digit strings (e.g. "0327600003126000023")
        assert not t["number"].isdigit()
        assert t["number"].startswith("MOS-")

    def test_missing_optional_fields_do_not_raise(self):
        minimal = {"id": 1, "name": "x", "company": {"name": "y"}, "startPrice": None,
                   "beginDate": None, "endDate": None}
        t = ks_dto_to_tender(minimal)
        assert t["number"] == "MOS-1"
        assert t["price"] is None

    def test_company_without_name_key_does_not_raise(self):
        # На случай если company-объект когда-нибудь придёт без name
        t = ks_dto_to_tender({"id": 2, "name": "x", "company": {"inn": "123"}})
        assert t["customer_name"] == ""

    def test_company_as_plain_string_still_works(self):
        # Fallback на случай если company всё же придёт строкой (другой
        # эндпоинт, старые фикстуры) — не должно ломаться.
        t = ks_dto_to_tender({"id": 3, "name": "x", "company": "Просто строка"})
        assert t["customer_name"] == "Просто строка"
