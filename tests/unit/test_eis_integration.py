"""Клиент интеграционного сервиса ЕИС: сборка запроса и разбор ответов.

Фикстуры повторяют настоящую структуру документов, снятую 20.09.2026 с
процедуры 0338300003326000140 (две заявки: 610207.99 и 614796.02).
"""
import datetime
import zipfile
from io import BytesIO

import pytest

from tender_sniper.sources.eis_integration import (
    DOC_PROTOCOL_FINAL,
    SUBSYSTEM_CONTRACTS,
    SUBSYSTEM_NOTICES,
    EisIntegrationError,
    build_request,
    iter_documents,
    parse_archive_urls,
    parse_contract,
    parse_protocol_final,
    raise_for_error,
)

EP = "http://zakupki.gov.ru/oos/EPtypes/1"
CMN = "http://zakupki.gov.ru/oos/common/1"


def _application(app_number, price, admitted, rating, commission_votes=3):
    # Голоса членов комиссии — ловушка: тег admitted здесь тот же, что и в
    # итоговом решении по заявке, но смысл другой.
    votes = "".join(
        f'<ns0:admissionResultInfo><ns0:commissionMemberInfo>'
        f'<ns1:memberNumber>{i}</ns1:memberNumber></ns0:commissionMemberInfo>'
        f'<ns0:admitted>true</ns0:admitted></ns0:admissionResultInfo>'
        for i in range(1, commission_votes + 1))
    return (
        f'<ns0:applicationInfo>'
        f'<ns0:commonInfo><ns0:appNumber>{app_number}</ns0:appNumber>'
        f'<ns0:admissionResultsInfo>{votes}</ns0:admissionResultsInfo></ns0:commonInfo>'
        f'<ns0:finalPrice>{price}</ns0:finalPrice>'
        f'<ns0:admittedInfo><ns0:appAdmittedInfo>'
        f'<ns0:admitted>{"true" if admitted else "false"}</ns0:admitted>'
        f'<ns0:appRating>{rating}</ns0:appRating>'
        f'</ns0:appAdmittedInfo></ns0:admittedInfo>'
        f'</ns0:applicationInfo>')


def protocol_xml(applications):
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<ns0:export xmlns:ns0="{EP}" xmlns:ns1="{CMN}">'
        f'<ns0:epProtocolEF2020Final>'
        f'<ns0:commonInfo>'
        f'<ns0:purchaseNumber>0338300003326000140</ns0:purchaseNumber>'
        f'<ns0:publishDTInEIS>2026-09-20T20:09:10+12:00</ns0:publishDTInEIS>'
        f'</ns0:commonInfo>'
        f'<ns0:customer><ns1:INN>4102003181</ns1:INN>'
        f'<ns1:fullName>ГБУЗ КК ВГБ</ns1:fullName></ns0:customer>'
        f'<ns0:protocolInfo><ns0:applicationsInfo>{"".join(applications)}'
        f'</ns0:applicationsInfo></ns0:protocolInfo>'
        f'</ns0:epProtocolEF2020Final></ns0:export>'
    ).encode("utf-8")


REAL = protocol_xml([
    _application("121942620", "610207.99", True, 1),
    _application("121942999", "614796.02", True, 2),
])


@pytest.mark.unit
class TestBuildRequest:
    def test_nested_elements_carry_no_prefix(self):
        """elementFormDefault="unqualified". С префиксами сервис отвечает
        кодом 28 «ошибка валидации по интеграционной схеме»."""
        xml = build_request(SUBSYSTEM_NOTICES, region="77",
                            doc_type=DOC_PROTOCOL_FINAL,
                            date=datetime.date(2026, 9, 17), token="t")
        assert "<selectionParams>" in xml and "<orgRegion>77</orgRegion>" in xml
        assert "ws:selectionParams" not in xml
        assert "base:" not in xml

    def test_only_root_element_is_qualified(self):
        xml = build_request(SUBSYSTEM_CONTRACTS, region="77", doc_type="contract",
                            date=datetime.date(2026, 9, 17), token="t")
        assert "<ws:getDocsByOrgRegionRequest" in xml

    def test_token_goes_into_soap_header(self):
        xml = build_request(SUBSYSTEM_NOTICES, reestr_number="03383", token="secret")
        assert "<individualPerson_token>secret</individualPerson_token>" in xml

    def test_by_reestr_number_uses_its_own_operation(self):
        xml = build_request(SUBSYSTEM_NOTICES, reestr_number="0338300003326000140")
        assert "getDocsByReestrNumberRequest" in xml
        assert "periodInfo" not in xml

    def test_region_mode_requires_all_three_fields(self):
        with pytest.raises(ValueError):
            build_request(SUBSYSTEM_NOTICES, region="77")


@pytest.mark.unit
class TestParseArchiveUrls:
    def test_reads_urls_wrapped_in_cdata(self):
        """Регрессия: ссылки приходят в CDATA, и разбор без её учёта
        показывал «архивов нет», когда они были."""
        xml = ('<dataInfo><archiveUrl><![CDATA[https://int.zakupki.gov.ru/a?x=1'
               ']]></archiveUrl><archiveUrl><![CDATA[https://int.zakupki.gov.ru/b'
               ']]></archiveUrl></dataInfo>')
        assert parse_archive_urls(xml) == ["https://int.zakupki.gov.ru/a?x=1",
                                           "https://int.zakupki.gov.ru/b"]

    def test_reads_urls_without_cdata(self):
        xml = "<archiveUrl>https://int.zakupki.gov.ru/a</archiveUrl>"
        assert parse_archive_urls(xml) == ["https://int.zakupki.gov.ru/a"]

    def test_no_data_is_an_empty_list_not_an_error(self):
        assert parse_archive_urls("<dataInfo><noData/></dataInfo>") == []


@pytest.mark.unit
class TestRaiseForError:
    def test_raises_with_service_code(self):
        xml = ("<errorInfo><code>28</code><message>Ошибка валидации полученного "
               "запроса по интеграционной схеме.</message></errorInfo>")
        with pytest.raises(EisIntegrationError) as exc:
            raise_for_error(xml)
        assert exc.value.code == "28"

    def test_silent_on_success(self):
        raise_for_error("<dataInfo><archiveUrl>x</archiveUrl></dataInfo>")


@pytest.mark.unit
class TestIterDocuments:
    def test_document_type_comes_from_file_name(self):
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("epProtocolEF2020Final_0338300003326000140_1_ABC.xml", "<a/>")
            zf.writestr("contract_2280115074926000021_0_DEF.xml", "<b/>")
            zf.writestr("readme.txt", "мусор")
        kinds = [kind for kind, _ in iter_documents(buf.getvalue())]
        assert kinds == ["epProtocolEF2020Final", "contract"]


@pytest.mark.unit
class TestParseProtocol:
    def test_reads_both_applications(self):
        """Веб-карточка той же процедуры показывала одного участника, в
        протоколе их два — ради этого сервис и нужен."""
        r = parse_protocol_final(REAL)
        assert r.purchase_number == "0338300003326000140"
        assert r.bids_submitted == 2
        assert [a.price for a in r.applications] == [610207.99, 614796.02]

    def test_commission_votes_are_not_counted_as_bids(self):
        """В заявке по три голоса членов комиссии с тем же тегом admitted.
        Если брать первый попавшийся, заявок «станет» втрое больше."""
        r = parse_protocol_final(REAL)
        assert r.bids_submitted == 2
        assert r.bids_admitted == 2

    def test_winner_is_taken_by_rating_not_by_price(self):
        r = parse_protocol_final(protocol_xml([
            _application("1", "900.00", True, 1),
            _application("2", "100.00", True, 2),
        ]))
        assert r.winner_price == 900.00

    def test_falls_back_to_cheapest_admitted_without_rating(self):
        r = parse_protocol_final(protocol_xml([
            _application("1", "900.00", True, 0),
            _application("2", "100.00", True, 0),
        ]))
        assert r.winner_price == 100.00

    def test_rejected_bid_is_counted_but_not_admitted(self):
        r = parse_protocol_final(protocol_xml([
            _application("1", "500.00", True, 1),
            _application("2", "400.00", False, 0),
        ]))
        assert (r.bids_submitted, r.bids_admitted) == (2, 1)
        assert r.winner_price == 500.00
        assert r.is_failed is False

    def test_procedure_without_admitted_bids_is_failed(self):
        r = parse_protocol_final(protocol_xml([_application("1", "500.00", False, 0)]))
        assert r.is_failed is True
        assert r.winner_price is None

    def test_customer_is_extracted(self):
        r = parse_protocol_final(REAL)
        assert r.customer_inn == "4102003181"

    def test_empty_protocol_does_not_crash(self):
        r = parse_protocol_final(protocol_xml([]))
        assert r.bids_submitted == 0 and r.is_failed is True


CONTRACT = (
    f'<ns0:export xmlns:ns0="{EP}" xmlns:ns1="{CMN}"><ns0:contract>'
    f'<ns0:regNum>2774310405926000057</ns0:regNum>'
    f'<ns0:notificationNumber>0373200018826000588</ns0:notificationNumber>'
    f'<ns0:price>100000.00</ns0:price>'
    f'<ns0:signDate>2026-09-15</ns0:signDate>'
    f'<ns0:supplier><ns1:INN>7709568163</ns1:INN>'
    f'<ns1:fullName>ГБУ Жилищник</ns1:fullName></ns0:supplier>'
    f'<ns0:products>'
    f'<ns0:product><ns0:OKPD2><ns1:code>95.29.19.221</ns1:code>'
    f'<ns1:name>Услуги по ремонту</ns1:name></ns0:OKPD2></ns0:product>'
    f'<ns0:product><ns0:OKPD2><ns1:code>25.99.23.000</ns1:code></ns0:OKPD2></ns0:product>'
    f'<ns0:product><ns0:OKPD2><ns1:code>95.29.19.221</ns1:code></ns0:OKPD2></ns0:product>'
    f'</ns0:products>'
    f'</ns0:contract></ns0:export>').encode("utf-8")


@pytest.mark.unit
class TestParseContract:
    def test_reads_supplier_inn(self):
        """ИНН победителя есть только здесь: в протоколе участник
        обезличен номером заявки."""
        c = parse_contract(CONTRACT)
        assert c["supplier_inn"] == "7709568163"
        assert c["price"] == "100000.00"

    def test_procedure_number_comes_from_notification_number(self):
        """Регрессия: поле называется notificationNumber. По
        purchaseNumber выходило пусто, и контракт не связывался с
        протоколом — то есть терялся ИНН победителя."""
        c = parse_contract(CONTRACT)
        assert c["purchase_number"] == "0373200018826000588"

    def test_reg_num_is_not_the_procedure_number(self):
        c = parse_contract(CONTRACT)
        assert c["reg_num"] == "2774310405926000057"
        assert c["reg_num"] != c["purchase_number"]

    def test_okpd2_collected_per_position_without_duplicates(self):
        c = parse_contract(CONTRACT)
        assert c["okpd2"] == ["95.29.19.221", "25.99.23.000"]

    def test_contract_without_products(self):
        xml = (f'<ns0:export xmlns:ns0="{EP}"><ns0:contract>'
               f'<ns0:regNum>1</ns0:regNum></ns0:contract></ns0:export>').encode("utf-8")
        assert parse_contract(xml)["okpd2"] == []
