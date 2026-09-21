"""Чтение страницы магазина: подходит ли товар и сколько он стоит.

Зачем отдельный слой. Поисковая выдача сама по себе бесполезна для
решения «идти ли в тендер»: в ней страницы категорий с ценником «от 250 ₽»
за весь раздел, а не цена нужного товара. Поэтому страницу-кандидата
нужно открыть и прочитать.

Разметку цен (schema.org) ставят далеко не все: проверено 19.09.2026 —
на страницах Комуса и Lionpack её нет вовсе, а наивный regex по «price»
в HTML вытащил из lionpack число 2026, то есть год. Поэтому читает модель.

Главная защита — от выдумывания. В первом же опыте страница вернула 403,
текста получилось 0 символов, и модель уверенно выдала «цена 450 ₽,
упаковка» на пустом входе. Для закупщика это худший вид ошибки: ложная
цена уходит в решение о ставке. Отсюда два правила, которые нельзя
ослаблять:

1. Модель не вызывается, если страница не отдалась (код не 200) или
   текста на ней меньше MIN_PAGE_TEXT.
2. Названная цена должна дословно встречаться в тексте страницы, иначе
   результат отбрасывается.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Маркетплейсы и агрегаторы: проверено 18.09.2026 — отвечают капчей
# «Вы не робот?» и читаемой страницы не отдают. Плюс их цены розничные и
# к закупке отношения не имеют. Это не «белый список наоборот»: сюда
# попадают только площадки, с которых мы физически не можем прочитать
# цену, а не поставщики, которые нам почему-то не нравятся.
UNREADABLE_DOMAINS = (
    'ozon.ru', 'wildberries.ru', 'market.yandex', 'aliexpress',
    'avito.ru', 'youla.ru', 'sbermegamarket', 'megamarket.ru',
)

# Ниже этого объёма страница считается непрочитанной (заглушка, капча,
# редирект). Порог эмпирический: у живых страниц магазинов 10 000+.
MIN_PAGE_TEXT = 800

# Сколько текста отдаём модели. Цена и название почти всегда в начале
# страницы, а платить за весь подвал с реквизитами незачем.
PAGE_TEXT_LIMIT = 5000

FETCH_TIMEOUT = 12

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')


@dataclass
class SpecCheck:
    """Сверка одной характеристики: что требовалось и что нашлось."""
    name: str
    required: str
    found: str = ''
    ok: Optional[bool] = None   # None = на странице не указано


@dataclass
class PageOffer:
    product: str
    price: Optional[float]
    unit: str = ''
    # Сколько штук в той единице, за которую названа цена. Нужно, чтобы
    # сравнивать предложения между собой: «12 ₽ за шт.» и «408 ₽ за упак.»
    # без этого несопоставимы, и более дорогое выглядит более дешёвым.
    pack_qty: Optional[int] = None
    is_from_price: bool = False   # «от 250 ₽» — ценник категории, не товара
    matches: bool = False
    # Построчная сверка характеристик. Без неё «подходит» — это слово без
    # доказательства: нельзя проверить, по чему именно сошлось и не
    # перепутан ли материал или размер.
    checks: List[SpecCheck] = field(default_factory=list)
    mismatch_reason: str = ''


def is_readable_domain(domain: str) -> bool:
    d = (domain or '').lower()
    return not any(bad in d for bad in UNREADABLE_DOMAINS)


def fetch_page_text(url: str) -> Optional[str]:
    """Текст страницы или None, если прочитать не удалось.

    None здесь — не «пустая страница», а «читать нечего»: вызывающий код
    обязан на этом остановиться и не спрашивать модель.
    """
    try:
        import requests
        from bs4 import BeautifulSoup

        # Проверку сертификата сначала НЕ отключаем: по этим ценам
        # принимается решение о ставке, и подменённая страница исказила бы
        # его. Но у части российских сайтов сертификаты выпущены УЦ
        # Минцифры, которого нет в хранилище контейнера, — для них
        # повторяем без проверки, иначе легальные магазины недоступны.
        try:
            resp = requests.get(url, headers={'User-Agent': _UA},
                                timeout=FETCH_TIMEOUT)
        except requests.exceptions.SSLError:
            import urllib3
            urllib3.disable_warnings()
            logger.debug(f'Страница {url[:60]}: сертификат не проверился, '
                         f'читаем без проверки')
            resp = requests.get(url, headers={'User-Agent': _UA},
                                timeout=FETCH_TIMEOUT, verify=False)

        if resp.status_code != 200:
            logger.debug(f'Страница {url[:60]} отдала {resp.status_code}')
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'noscript']):
            tag.decompose()
        text = ' '.join(soup.get_text(' ', strip=True).split())
        if len(text) < MIN_PAGE_TEXT:
            logger.debug(f'Страница {url[:60]}: текста {len(text)}, пропускаем')
            return None
        return text
    except Exception as e:
        logger.debug(f'Страница {url[:60]} недоступна: {type(e).__name__}')
        return None


def _price_is_on_page(price: float, page_text: str) -> bool:
    """Встречается ли названная цена в тексте страницы.

    Защита от выдуманных цен. Сравниваем по целой части и без пробелов:
    на странице цена бывает «1 250,00 ₽», «1250руб», «1 250».
    """
    if price is None:
        return False
    digits = re.sub(r'[\s ]', '', page_text)
    whole = str(int(price))
    if whole in digits:
        return True
    # Цена могла быть округлена моделью: допускаем расхождение в рубль.
    return any(str(int(price) + delta) in digits for delta in (-1, 1))


_SPEC_PROMPT = """Из строки технического задания нужно выделить требования к товару.

СТРОКА ТЗ: {position}

Выдели ТОЛЬКО те характеристики, которые в строке действительно названы. Ничего не добавляй от себя.
Типичные характеристики: предмет, материал, размер, цвет, плотность/вес, стерильность, ГОСТ/стандарт, количество, назначение.

Ответь строго JSON:
{{"requirements": [{{"name": "материал", "value": "нитрил"}}, {{"name": "размер", "value": "M"}}]}}"""


async def parse_requirements(position: str) -> List[Dict[str, str]]:
    """Требования к товару из строки ТЗ.

    Выделяем ОДИН раз на позицию, а не при разборе каждой страницы:
    иначе набор требований плыл бы от страницы к странице и сравнивать
    кандидатов между собой было бы не с чем.
    """
    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key or not position:
        return []

    import asyncio

    from tender_sniper.openai_client import make_openai_client

    def _call():
        client = make_openai_client(api_key)
        resp = client.chat.completions.create(
            model='gpt-4o-mini', max_tokens=300, temperature=0,
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system', 'content': 'Ты выделяешь требования из ТЗ. Только JSON.'},
                {'role': 'user', 'content': _SPEC_PROMPT.format(position=position[:400])},
            ],
        )
        return resp.choices[0].message.content

    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, _call)
        data = json.loads(raw)
        out = []
        for r in (data.get('requirements') or [])[:12]:
            name = str(r.get('name') or '').strip()[:40]
            value = str(r.get('value') or '').strip()[:80]
            if name and value:
                out.append({'name': name, 'value': value})
        return out
    except Exception as e:
        logger.debug(f'Не удалось выделить требования: {e}')
        return []


_PROMPT = """Со страницы интернет-магазина нужно понять, продаётся ли там нужный товар и по какой цене.

НУЖЕН ТОВАР: {position}

ТРЕБОВАНИЯ ИЗ ТЗ (сверь каждое):
{requirements}

ПРАВИЛА:
- По КАЖДОМУ требованию верни, что написано на странице (found) и сходится ли (ok). Если на странице характеристика не указана — found: "", ok: null. Не додумывай.
- matches = false, если хотя бы одно требование ЯВНО противоречит (другой материал, другой размер). Молчание страницы противоречием НЕ считается: нет характеристики — ok: null, и это не повод для matches: false.
- Не считай противоречием разную запись одного и того же: габарит с толщиной («595х595х25») сходится с «595х595»; монтажный размер («600х600») сходится с размером панели «595х595»; диапазон, который содержит требуемое значение («35-52 Вт» при требовании 40 Вт), сходится.
- Класс защиты от поражения током (I, II, III) и степень защиты оболочки (IP20, IP40, IP65) — РАЗНЫЕ характеристики. Не сравнивай одну с другой и не подставляй одну вместо другой.
- price указывай ТОЛЬКО если цена явно написана на странице. Если цены нет — null.
- Ничего не додумывай: нет данных — null или пустая строка.
- is_from_price = true, если это цена «от ...» для раздела или диапазон, а не цена конкретного товара.
- matches = true только если товар на странице действительно соответствует нужному (тот же предмет и ключевые характеристики).
- pack_qty — сколько ШТУК (пар, листов) в той единице, за которую указана цена. Цена за штуку -> 1. Цена за упаковку 100 пар -> 100. Пачка 500 листов -> 500. Не понятно -> null.

СТРАНИЦА:
{page}

Ответь строго JSON без пояснений:
{{"matches": true|false, "product": "название товара на странице", "price": число|null, "unit": "за что цена (шт/упак/пачка/коробка)", "pack_qty": число|null, "is_from_price": true|false, "mismatch_reason": "чем не подошёл, если matches=false", "checks": [{{"name": "материал", "found": "нитрил", "ok": true}}]}}"""


# Потолок ответа считается от числа требований, а не берётся плоским.
# Плоские 250 токенов означали: чем подробнее позиция, тем вернее срыв —
# на каждое требование модель возвращает строку в checks, и ответ
# обрывается на полуслове, а json.loads падает. Со стороны это выглядело
# как «страницу не удалось разобрать».
#
# Замер 21.09.2026 на закупке 0373100128326000093 (9 требований), три
# страницы: при 250 все три оборвались (finish_reason=length), при 900
# разобрались все три.
_REPLY_BASE = 260       # поля вне checks: product, price, unit, причина
_REPLY_PER_CHECK = 80   # одна строка checks с запасом на длинное found
_REPLY_CEILING = 1200


def _reply_budget(req_count: int) -> int:
    return min(_REPLY_CEILING, _REPLY_BASE + _REPLY_PER_CHECK * req_count)


# «Не указано» словами — то же самое, что пустое found. Модель пишет и
# так: замер 21.09.2026 дал отказ «материал не соответствует
# (поликарбонат vs. не указано)», то есть расхождение с отсутствием.
_BLANK_FOUND = ('не указан', 'не указано', 'не указана', 'нет данных',
                'отсутствует', 'неизвестно', 'н/д', '-', '—')


def _is_blank(found: str) -> bool:
    text = (found or '').strip().lower().rstrip('.')
    return not text or text in _BLANK_FOUND


async def read_offer(position: str, page_text: str,
                     requirements: Optional[List[Dict[str, str]]] = None) -> Optional[PageOffer]:
    """Читает страницу моделью. None, если разобрать не удалось.

    Вызывать только с непустым page_text из fetch_page_text.
    """
    if not page_text or len(page_text) < MIN_PAGE_TEXT:
        # Повторная проверка намеренно: именно пустой вход заставил модель
        # выдумать цену в первом же опыте.
        return None

    api_key = os.getenv('OPENAI_API_KEY')
    if not api_key:
        return None

    import asyncio

    from tender_sniper.openai_client import make_openai_client

    reqs = requirements or []
    req_text = ('\n'.join(f"- {r['name']}: {r['value']}" for r in reqs)
                if reqs else '(явных требований не выделено)')
    prompt = _PROMPT.format(position=position[:300], requirements=req_text,
                            page=page_text[:PAGE_TEXT_LIMIT])

    def _call():
        client = make_openai_client(api_key)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            max_tokens=_reply_budget(len(reqs)),
            temperature=0,
            response_format={'type': 'json_object'},
            messages=[
                {'role': 'system',
                 'content': 'Ты извлекаешь данные о товаре со страницы магазина. '
                            'Отвечаешь только JSON. Ничего не выдумываешь.'},
                {'role': 'user', 'content': prompt},
            ],
        )
        return resp.choices[0].message.content

    try:
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, _call)
        data = json.loads(raw)
    except Exception as e:
        logger.debug(f'Не удалось разобрать страницу моделью: {e}')
        return None

    price = data.get('price')
    try:
        price = float(price) if price is not None else None
    except (TypeError, ValueError):
        price = None

    # Антивыдумка: цены, которой нет на странице, не существует.
    if price is not None and not _price_is_on_page(price, page_text):
        logger.info(f'Цена {price} не найдена в тексте страницы — отбрасываем')
        price = None

    pack_qty = data.get('pack_qty')
    try:
        pack_qty = int(pack_qty) if pack_qty else None
        if pack_qty is not None and pack_qty <= 0:
            pack_qty = None
    except (TypeError, ValueError):
        pack_qty = None

    # Сверку строим от НАШЕГО списка требований, а не от того, что решила
    # вернуть модель: иначе она могла бы умолчать о неудобной строке.
    found_by_name = {}
    for c in (data.get('checks') or []):
        key = str(c.get('name') or '').strip().lower()
        if key:
            found_by_name[key] = c
    checks = []
    for r in reqs:
        c = found_by_name.get(r['name'].strip().lower(), {})
        ok = c.get('ok')
        found = str(c.get('found') or '')[:80]
        # Нечего было сравнивать — значит и расхождения нет. Модель это
        # правило нарушает даже когда оно написано в промпте: замер
        # 21.09.2026 по закупке 0373100128326000093 дал ok=false при
        # пустом found с пояснением «материал не указан». Такие отказы
        # обнуляли подбор, поэтому решает код, а не модель.
        if _is_blank(found):
            found, ok = '', None
        checks.append(SpecCheck(
            name=r['name'],
            required=r['value'],
            found=found,
            ok=None if ok is None else bool(ok),
        ))

    return PageOffer(
        checks=checks,
        mismatch_reason=str(data.get('mismatch_reason') or '')[:200],
        product=str(data.get('product') or '')[:200],
        price=price,
        unit=str(data.get('unit') or '')[:40],
        pack_qty=pack_qty,
        is_from_price=bool(data.get('is_from_price')),
        matches=bool(data.get('matches')),
    )
