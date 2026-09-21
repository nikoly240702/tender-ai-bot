"""Клиент интеграционного сервиса ЕИС (getDocsIP) — массовая выгрузка.

Заменяет отключённый 01.01.2025 FTP открытых данных. Один запрос отдаёт
архив с сотнями документов вместо поштучного обхода карточек на сайте.

Зачем отдельный источник, когда есть парсер сайта: **публичный сайт ЕИС
блокирует хостинговые IP, а интеграционный сервис — нет**. Замер
20.09.2026 через прокси: `zakupki.gov.ru` — 0 успешных из 9,
`int.zakupki.gov.ru` — 5 из 5. Плюс данных в протоколе больше: по
процедуре 0338300003326000140 сайт показал одного участника, протокол
из сервиса — двух, с ценами обоих.

Грабли, на которых здесь легко потерять полдня:

1. **Клиент не должен предлагать ГОСТ-шифры.** Сервер их выберет, и
   рукопожатие оборвётся на session ticket. В OpenSSL (боевой сервер)
   ГОСТа нет и всё работает само; на macOS python слинкован с LibreSSL,
   где ГОСТ есть, — оттуда нужен `ALL:!GOST:!aGOST:!kGOST`, причём
   одного `!GOST` мало.
2. **`elementFormDefault="unqualified"`** — все вложенные элементы БЕЗ
   префиксов, префикс только у корневого элемента запроса. Иначе
   сервис отвечает кодом 28 «ошибка валидации по интеграционной схеме».
3. **Ссылки на архивы приходят в CDATA** — наивный regex их не видит и
   показывает «архивов нет», хотя они есть.
4. Прямого доступа к `int.zakupki.gov.ru` с сервера нет, только через
   прокси — как и к основному сайту.

Документация: «Инструкция по использованию сервиса отдачи информации и
документов ЕИС», раздел 5.3. Коды подсистем и типы документов для
44-ФЗ — в Альбоме ТФФ, раздел 2.9.19; в самой инструкции их нет, часть
восстановлена эмпирически (см. DOC_TYPES).
"""
import datetime as _dt
import logging
import os
import re
import ssl
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from typing import Dict, Iterator, List, Optional, Tuple

import requests

try:
    from defusedxml.ElementTree import fromstring as _xml_fromstring
except ImportError:  # окружение без defusedxml — работаем, но предупреждаем
    logging.getLogger(__name__).warning(
        "defusedxml не установлен, XML ЕИС разбирается небезопасным парсером")
    _xml_fromstring = ET.fromstring

logger = logging.getLogger(__name__)

SERVICE_URL = "https://int.zakupki.gov.ru/eis-integration/services/getDocsIP"
PROXY_ENV_VARS = ["PROXY_URL", "PROXY_URL_2", "PROXY_URL_3", "PROXY_URL_4", "PROXY_URL_5"]
TOKEN_ENV_VAR = "EIS_INTEGRATION_TOKEN"

# Сертификат int.zakupki.gov.ru выпущен УЦ Федерального казначейства, а тот
# — корневым Минцифры России; в системных хранилищах этой цепочки нет, и
# без неё проверка падает с «unable to get local issuer certificate».
# Отключать проверку нельзя: канал идёт через сторонний прокси, то есть
# ровно через того, кто и мог бы подменить трафик. Поэтому возим цепочку с
# собой — сертификаты УЦ публичны, скачаны по ссылкам из расширения AIA.
CA_BUNDLE = os.getenv("EIS_CA_BUNDLE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "certs", "eis_ca_chain.pem")

WS_NS = "http://zakupki.gov.ru/fz44/get-docs-ip/ws"
DEFAULT_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 180

# Коды подсистем из Альбома ТФФ. Здесь только те, что нужны аналитике;
# полный список (39 значений) — в getDocsIP.xsd, тип commonSubsystemType.
SUBSYSTEM_NOTICES = "PRIZ"    # извещения и протоколы
SUBSYSTEM_CONTRACTS = "RGK"   # реестр государственных контрактов

# Типы документов (documentType44). Восстановлены из имён файлов внутри
# архива: запрос по реестровому номеру отдаёт все документы процедуры, и
# каждый файл назван своим типом. Угадать их нельзя — «protocols»,
# «epProtocolEF44», «purchaseProtocol» дают noData.
DOC_NOTICE_EF = "epNotificationEF2020"
DOC_PROTOCOL_SUBMIT = "epProtocolEF2020SubmitOffers"  # протокол подачи заявок
DOC_PROTOCOL_FINAL = "epProtocolEF2020Final"          # протокол подведения итогов
DOC_PLACEMENT_RESULT = "fcsPlacementResult"
DOC_PROPOSALS_RESULT = "fcsProposalsResult"
DOC_CONTRACT = "contract"

_CREDENTIALS_RE = re.compile(r"://[^/@\s]+@")
# Ссылка на архив приходит завёрнутой в CDATA; группа берёт содержимое и с
# ним, и без него.
_ARCHIVE_URL_RE = re.compile(
    r"<archiveUrl>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</archiveUrl>", re.S)


class _NoGostAdapter(requests.adapters.HTTPAdapter):
    """TLS без ГОСТ-шифров в предложении клиента.

    Если клиент предложит ГОСТ, сервер ЕИС его выберет, и рукопожатие
    оборвётся на session ticket — без внятной ошибки, просто EOF. В
    OpenSSL ГОСТа нет, и адаптер там ничего не меняет; он нужен для
    macOS, где python слинкован с LibreSSL. Одного `!GOST` мало —
    LibreSSL его игнорирует, нужны все три имени.
    """

    _CIPHERS = "ALL:!GOST:!aGOST:!kGOST"

    def _context(self):
        ctx = ssl.create_default_context(cafile=CA_BUNDLE)
        try:
            ctx.set_ciphers(self._CIPHERS)
        except ssl.SSLError:  # сборка без ГОСТ — набор не нужен
            pass
        return ctx

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._context()
        return super().proxy_manager_for(*args, **kwargs)


class EisIntegrationError(RuntimeError):
    """Сервис ответил ошибкой. `code` — код из errorInfo, если он был."""

    def __init__(self, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.code = code


def _mask(text: str) -> str:
    """Прячет user:pass@ — requests подставляет полный URL прокси в текст
    ошибки, и без этого credentials утекут в лог."""
    return _CREDENTIALS_RE.sub("://***@", text)


def _proxies() -> List[str]:
    return [v for v in (os.getenv(n, "").strip() for n in PROXY_ENV_VARS) if v]


def _local(tag: str) -> str:
    """Имя тега без namespace: в документах ЕИС их несколько вперемешку."""
    return tag.split("}")[-1]


def parse_xml(xml_bytes: bytes):
    """Дерево документа ЕИС. Единая точка разбора: XML приезжает из
    внешнего источника, поэтому парсер должен быть защищённым, и
    вызывающим модулям незачем это знать."""
    return _xml_fromstring(xml_bytes.decode("utf-8", "ignore"))


def build_request(subsystem: str, *, region: Optional[str] = None,
                  doc_type: Optional[str] = None, date: Optional[_dt.date] = None,
                  reestr_number: Optional[str] = None,
                  token: str = "") -> str:
    """Конверт SOAP. Вложенные элементы намеренно без префиксов — см. шапку.

    Два режима: по региону с типом документа и датой, либо по реестровому
    номеру процедуры (тогда приезжают все её документы сразу).
    """
    now = _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()
    head = (f'<index><id>{uuid.uuid4()}</id>'
            f'<createDateTime>{now}</createDateTime><mode>PROD</mode></index>')

    if reestr_number:
        operation = "getDocsByReestrNumberRequest"
        params = (f'<subsystemType>{subsystem}</subsystemType>'
                  f'<reestrNumber>{reestr_number}</reestrNumber>')
    else:
        if not (region and doc_type and date):
            raise ValueError("нужны region, doc_type и date либо reestr_number")
        operation = "getDocsByOrgRegionRequest"
        params = (f'<orgRegion>{region}</orgRegion>'
                  f'<subsystemType>{subsystem}</subsystemType>'
                  f'<documentType44>{doc_type}</documentType44>'
                  f'<periodInfo><exactDate>{date.isoformat()}</exactDate></periodInfo>')

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soapenv:Header><individualPerson_token>{token}</individualPerson_token>'
        '</soapenv:Header><soapenv:Body>'
        f'<ws:{operation} xmlns:ws="{WS_NS}">'
        f'{head}<selectionParams>{params}</selectionParams>'
        f'</ws:{operation}></soapenv:Body></soapenv:Envelope>'
    )


def parse_archive_urls(response_xml: str) -> List[str]:
    """Ссылки на архивы из ответа. Пустой список — это штатный `noData`."""
    return [u.strip() for u in _ARCHIVE_URL_RE.findall(response_xml) if u.strip()]


def raise_for_error(response_xml: str) -> None:
    code = re.search(r"<code>(\d+)</code>", response_xml)
    if not code:
        return
    message = re.search(r"<message>([^<]*)</message>", response_xml)
    raise EisIntegrationError(
        (message.group(1) if message else "ошибка сервиса ЕИС").strip(),
        code.group(1))


class EisIntegrationClient:
    """Запрашивает архивы и скачивает их. Прокси перебираются по кругу —
    копия подхода из mos_portal_client, сознательно без общей абстракции:
    у источников разные схемы ошибок и разный смысл повторов."""

    def __init__(self, token: Optional[str] = None,
                 proxies: Optional[List[str]] = None,
                 session: Optional[requests.Session] = None):
        self.token = token or os.getenv(TOKEN_ENV_VAR, "").strip()
        self.proxies = proxies if proxies is not None else _proxies()
        self.session = session or requests.Session()
        # Один адаптер на оба протокола: к сервису ходим по https, но
        # прокси задан как http:// — requests выбирает адаптер по схеме
        # целевого URL, поэтому хватает https, а http оставляем для
        # единообразия, если адрес сервиса когда-нибудь сменят.
        adapter = _NoGostAdapter()
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    def _post(self, body: str) -> str:
        headers = {"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'}
        last: Optional[Exception] = None
        # Без прокси тоже пробуем: с машины в РФ на резидентном адресе
        # сервис доступен напрямую, это рабочий сценарий для отладки.
        for proxy in (self.proxies or [None]):
            try:
                response = self.session.post(
                    SERVICE_URL, data=body.encode("utf-8"), headers=headers,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    timeout=DEFAULT_TIMEOUT, verify=CA_BUNDLE)
                response.raise_for_status()
                return response.text
            except Exception as exc:  # noqa: BLE001 — перебираем прокси дальше
                last = exc
                logger.warning("ЕИС-интеграция: прокси %s не сработал: %s",
                               (proxy or "прямое соединение").split("@")[-1],
                               _mask(str(exc))[:200])
        raise EisIntegrationError(
            "ни один прокси не дал ответа: %s" % _mask(str(last))[:200])

    def request_archives(self, subsystem: str, doc_type: str, region: str,
                         date: _dt.date) -> List[str]:
        """Архивы по региону, типу документа и дате. Пусто — данных нет."""
        xml = self._post(build_request(subsystem, region=region,
                                       doc_type=doc_type, date=date,
                                       token=self.token))
        raise_for_error(xml)
        urls = parse_archive_urls(xml)
        logger.info("ЕИС-интеграция: %s/%s регион %s за %s — архивов %d",
                    subsystem, doc_type, region, date, len(urls))
        return urls

    def request_archives_by_number(self, subsystem: str,
                                   reestr_number: str) -> List[str]:
        """Все документы одной процедуры. Полезно и само по себе, и чтобы
        узнать реальные имена типов документов — они в именах файлов."""
        xml = self._post(build_request(subsystem, reestr_number=reestr_number,
                                       token=self.token))
        raise_for_error(xml)
        return parse_archive_urls(xml)

    def download(self, archive_url: str) -> bytes:
        """Скачивает архив. Токен нужен и здесь, не только в SOAP-запросе."""
        headers = {"individualPerson_token": self.token}
        last: Optional[Exception] = None
        for proxy in (self.proxies or [None]):
            try:
                response = self.session.get(
                    archive_url, headers=headers,
                    proxies={"http": proxy, "https": proxy} if proxy else None,
                    timeout=DOWNLOAD_TIMEOUT, verify=CA_BUNDLE)
                response.raise_for_status()
                return response.content
            except Exception as exc:  # noqa: BLE001
                last = exc
        raise EisIntegrationError("архив не скачался: %s" % _mask(str(last))[:200])


def iter_documents(archive: bytes) -> Iterator[Tuple[str, bytes]]:
    """(тип документа, содержимое) для каждого файла архива.

    Тип берётся из имени файла: ЕИС называет их
    `<documentType44>_<реестровый номер>_<версия>_<uid>.xml`.
    """
    with zipfile.ZipFile(BytesIO(archive)) as zf:
        for name in zf.namelist():
            if not name.lower().endswith(".xml"):
                continue
            yield name.split("/")[-1].split("_")[0], zf.read(name)


@dataclass
class Application:
    """Одна заявка в протоколе. Участник обезличен номером — ИНН
    победителя появляется только в реестре контрактов, после заключения."""
    app_number: str
    price: Optional[float] = None
    admitted: Optional[bool] = None
    rating: Optional[int] = None


@dataclass
class ProtocolResult:
    """Разобранный протокол подведения итогов."""
    purchase_number: str = ""
    protocol_date: Optional[str] = None
    customer_inn: Optional[str] = None
    customer_name: Optional[str] = None
    applications: List[Application] = field(default_factory=list)

    @property
    def bids_submitted(self) -> int:
        return len(self.applications)

    @property
    def bids_admitted(self) -> int:
        return sum(1 for a in self.applications if a.admitted)

    @property
    def winner_price(self) -> Optional[float]:
        """Цена заявки с рейтингом 1. Если рейтингов нет — минимальная из
        допущенных: на сортировку по цене полагаться нельзя, у некоторых
        процедур побеждает не самая дешёвая заявка."""
        rated = [a for a in self.applications if a.rating == 1 and a.price is not None]
        if rated:
            return rated[0].price
        prices = [a.price for a in self.applications if a.admitted and a.price is not None]
        return min(prices) if prices else None

    @property
    def is_failed(self) -> bool:
        """Несостоявшейся считаем процедуру без единой допущенной заявки."""
        return self.bids_admitted == 0


def _first_text(element, tag: str) -> Optional[str]:
    for node in element.iter():
        if _local(node.tag) == tag and (node.text or "").strip():
            return node.text.strip()
    return None


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def parse_protocol_final(xml_bytes: bytes) -> ProtocolResult:
    """Разбирает epProtocolEF2020Final.

    Структура (проверено на 0338300003326000140):
      protocolInfo/applicationsInfo/applicationInfo
        commonInfo/appNumber
        finalPrice
        admittedInfo/appAdmittedInfo/{admitted,appRating}

    ⚠️ `admitted` встречается в документе дважды в разном смысле: внутри
    `commonInfo/admissionResultsInfo` это голос КАЖДОГО члена комиссии, и
    таких узлов столько, сколько человек в комиссии. Итоговое решение по
    заявке лежит только в `admittedInfo/appAdmittedInfo`. Поэтому берём
    его адресно, а не первым попавшимся `admitted` в поддереве.
    """
    root = parse_xml(xml_bytes)
    result = ProtocolResult(
        purchase_number=_first_text(root, "purchaseNumber") or "",
        protocol_date=_first_text(root, "publishDTInEIS"),
        customer_inn=_first_text(root, "INN"),
        customer_name=_first_text(root, "fullName"),
    )

    for app_node in (n for n in root.iter() if _local(n.tag) == "applicationInfo"):
        children = {_local(c.tag): c for c in app_node}
        admitted_block = children.get("admittedInfo")
        admitted = rating = None
        if admitted_block is not None:
            admitted_text = _first_text(admitted_block, "admitted")
            admitted = admitted_text == "true" if admitted_text else None
            rating_text = _first_text(admitted_block, "appRating")
            rating = int(rating_text) if rating_text and rating_text.isdigit() else None

        common = children.get("commonInfo")
        result.applications.append(Application(
            app_number=(_first_text(common, "appNumber") if common is not None else "") or "",
            price=_to_float(children["finalPrice"].text if "finalPrice" in children else None),
            admitted=admitted,
            rating=rating,
        ))
    return result


def parse_contract(xml_bytes: bytes) -> Dict[str, object]:
    """Ключевые поля контракта.

    Отсюда берётся ИНН победителя, которого в протоколе нет: там участник
    обезличен номером заявки.

    ⚠️ Номер процедуры в контракте называется `notificationNumber`, а не
    `purchaseNumber` — по последнему поле выходило пустым, и контракт не
    связывался с протоколом. `regNum` здесь — номер записи в реестре
    контрактов, это другое число.

    ОКПД2 лежит по позициям (`products/product/OKPD2/code`), поэтому
    возвращается списком: у одного контракта их может быть несколько.
    """
    root = parse_xml(xml_bytes)
    okpd2 = []
    for node in root.iter():
        if _local(node.tag) != "OKPD2":
            continue
        for child in node:
            if _local(child.tag) == "code" and (child.text or "").strip():
                code = child.text.strip()
                if code not in okpd2:
                    okpd2.append(code)
    return {
        "reg_num": _first_text(root, "regNum"),
        "purchase_number": (_first_text(root, "notificationNumber")
                            or _first_text(root, "purchaseNumber")),
        "price": _first_text(root, "price"),
        "sign_date": _first_text(root, "signDate"),
        "supplier_inn": _first_text(root, "INN"),
        "supplier_name": _first_text(root, "fullName"),
        "okpd2": okpd2,
    }
