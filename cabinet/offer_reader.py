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
from dataclasses import dataclass
from typing import Optional

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
class PageOffer:
    product: str
    price: Optional[float]
    unit: str = ''
    is_from_price: bool = False   # «от 250 ₽» — ценник категории, не товара
    matches: bool = False


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


_PROMPT = """Со страницы интернет-магазина нужно понять, продаётся ли там нужный товар и по какой цене.

НУЖЕН ТОВАР: {position}

ПРАВИЛА:
- price указывай ТОЛЬКО если цена явно написана на странице. Если цены нет — null.
- Ничего не додумывай: нет данных — null или пустая строка.
- is_from_price = true, если это цена «от ...» для раздела или диапазон, а не цена конкретного товара.
- matches = true только если товар на странице действительно соответствует нужному (тот же предмет и ключевые характеристики).

СТРАНИЦА:
{page}

Ответь строго JSON без пояснений:
{{"matches": true|false, "product": "название товара на странице", "price": число|null, "unit": "за что цена (шт/упак/пачка/коробка)", "is_from_price": true|false}}"""


async def read_offer(position: str, page_text: str) -> Optional[PageOffer]:
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

    prompt = _PROMPT.format(position=position[:300], page=page_text[:PAGE_TEXT_LIMIT])

    def _call():
        client = make_openai_client(api_key)
        resp = client.chat.completions.create(
            model='gpt-4o-mini',
            max_tokens=250,
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

    return PageOffer(
        product=str(data.get('product') or '')[:200],
        price=price,
        unit=str(data.get('unit') or '')[:40],
        is_from_price=bool(data.get('is_from_price')),
        matches=bool(data.get('matches')),
    )
