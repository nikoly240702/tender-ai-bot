"""Цифровой закупщик: «где купить» под позиции ТЗ тендера.

Порядок поиска задан владельцем (18.09.2026):

1. **Свой каталог** (own_products) — первый и главный узел. Это не только
   экономия: сопоставление «позиция тендера ↔ номенклатура компании» —
   то, что предполагается продавать компаниям с большим собственным
   каталогом. Поэтому узел самостоятельный, а не побочный для своих нужд.
2. **Кэш прошлых поисков** — система учится на том, что уже искала:
   повторяющаяся позиция («бумага А4 80 г/м2» встречается в сотнях
   закупок) не проходит полный цикл заново.
3. **Общий веб-поиск** — без белого списка поставщиков: ограничивать
   выдачу заранее известными сайтами значит не находить новых.

Цены с сайтов розничные, поэтому результат — **оценка сверху**
(«дороже этого точно не будет»), а не расчёт маржи. Реальная закупочная
цена приходит от поставщика по договору, и именно она попадает в
калькулятор карточки.
"""
import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from database import DatabaseSession, OwnProduct, ProductSearchCache

logger = logging.getLogger(__name__)

# Сколько живёт запись кэша. Цены на сайтах меняются, но не ежедневно;
# две недели — компромисс между свежестью и экономией запросов.
CACHE_TTL_DAYS = 14

# Слова, которые есть почти в каждой позиции ТЗ и потому ничего не
# различают. Оставлять их в ключе кэша — значит считать «поставка бумаги»
# и «бумага» разными запросами.
STOPWORDS = {
    'поставка', 'поставки', 'закупка', 'приобретение', 'товар', 'товара',
    'изделие', 'изделия', 'продукция', 'продукции', 'шт', 'штук', 'штука',
    'ед', 'единиц', 'комплект', 'комплекта', 'упак', 'упаковка', 'набор',
    'для', 'или', 'над', 'под', 'при', 'без', 'тип', 'вид', 'также',
    'соответствии', 'требованиями', 'наименование', 'характеристики',
}

_TOKEN_RE = re.compile(r'[а-яёa-z0-9]+', re.IGNORECASE)

# Русские окончания, от длинных к коротким. Порядок важен: иначе «ами»
# срежется как «и» и «бумагами» не сойдётся с «бумага».
#
# Почему не pymorphy2: библиотека в проекте не установлена, тянет словари
# на десятки мегабайт и не поддерживается на новых версиях Python, а нам
# нужна не настоящая лемматизация, а одинаковое усечение — чтобы «бумаги»
# и «бумага» дали ОДИН ключ кэша. Здесь же нельзя переиспользовать
# _same_root из ai_relevance_checker: он сравнивает два слова между собой,
# а ключу нужна единственная каноничная форма.
_ENDINGS = (
    'ого', 'его', 'ому', 'ему', 'ыми', 'ими', 'ами', 'ями', 'ных', 'ний',
    'ая', 'яя', 'ое', 'ее', 'ые', 'ие', 'ый', 'ий', 'ой', 'ей', 'ым', 'им',
    'ых', 'их', 'ую', 'юю', 'ом', 'ем', 'ов', 'ев', 'ах', 'ях', 'ам', 'ям',
    'а', 'я', 'о', 'е', 'ы', 'и', 'у', 'ю', 'ь', 'й',
)
# Ниже этой длины не режем: у коротких слов усечение съедает корень.
_MIN_STEM = 4


def _stem(word: str) -> str:
    """Грубое усечение окончания. Задача — стабильность, не точность.

    Известное ограничение: беглые гласные не берутся («перчатки» →
    «перчатк», но «перчаток» → «перчаток»), для этого нужен словарь.
    Цена ошибки невелика: разойдутся ключи кэша, и мы лишний раз сходим
    в поиск — результат от этого не портится, теряется только экономия.
    """
    if len(word) <= _MIN_STEM or not re.search(r'[а-яё]', word):
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= _MIN_STEM - 1:
            return word[:-len(ending)]
    return word

# «420 руб», «1 250,50 ₽», «от 99 р.» — цена в сниппете выдачи.
_PRICE_RE = re.compile(
    r'(\d[\d\s ]{0,12}(?:[.,]\d{1,2})?)\s*(?:₽|руб|р\.)', re.IGNORECASE)


@dataclass
class Offer:
    """Найденный вариант покупки."""
    title: str
    url: str
    domain: str = ''
    price: Optional[float] = None
    snippet: str = ''
    source: str = 'web'          # web | catalog | cache


@dataclass
class PositionResult:
    position: str
    offers: List[Offer] = field(default_factory=list)
    source: str = 'none'         # catalog | cache | web | none
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'position': self.position,
            'source': self.source,
            'error': self.error,
            'offers': [asdict(o) for o in self.offers],
        }


def tokenize(text: str) -> List[str]:
    """Значащие слова позиции: без стоп-слов, усечённые до основы.

    Усечение обязательно: в тендерах одна и та же позиция пишется в разных
    падежах («поставка бумаги офисной» и «бумага офисная»), и без него кэш
    считал бы это разными запросами — то есть не учился бы на повторах.
    """
    tokens = [t.lower() for t in _TOKEN_RE.findall(text or '')]
    return [_stem(t) for t in tokens if len(t) > 1 and t not in STOPWORDS]


def normalize_position(text: str) -> str:
    """Каноничный вид позиции — основа ключа кэша.

    Порядок слов в ТЗ произвольный («бумага офисная А4» и «А4 бумага
    офисная» — одно и то же), поэтому токены сортируются: иначе кэш
    считал бы это разными запросами и учиться было бы не на чем.
    """
    return ' '.join(sorted(set(tokenize(text))))


def cache_key(text: str) -> str:
    normalized = normalize_position(text)
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def extract_price(text: str) -> Optional[float]:
    """Первая цена из текста сниппета. None, если цены нет.

    Это ориентир из выдачи, а не факт: на странице цена могла измениться,
    а в сниппете попасться цена другого товара.
    """
    m = _PRICE_RE.search(text or '')
    if not m:
        return None
    raw = m.group(1).replace(' ', '').replace(' ', '').replace(',', '.')
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def catalog_match_score(position: str, product_text: str) -> float:
    """Насколько позиция ТЗ похожа на запись каталога — доля совпавших слов.

    Считаем от позиции, а не от записи каталога: у каталожной записи
    описание бывает длиннее («бумага А4 80 г/м2 белизна 146% 500 л.»), и
    деление на её длину занижало бы совпадение для короткой позиции.
    """
    pos_tokens = set(tokenize(position))
    if not pos_tokens:
        return 0.0
    prod_tokens = set(tokenize(product_text))
    if not prod_tokens:
        return 0.0
    return len(pos_tokens & prod_tokens) / len(pos_tokens)


# Порог совпадения с каталогом. 0.6 подобран как «больше половины
# значащих слов позиции нашлись» — ниже начинается мусор вроде совпадения
# по одному слову «бумага».
CATALOG_MATCH_THRESHOLD = 0.6


async def match_catalog(company_id: int, position: str,
                        limit: int = 5) -> List[Offer]:
    """Ищет позицию в собственном каталоге компании."""
    async with DatabaseSession() as session:
        products = (await session.scalars(
            select(OwnProduct).where(OwnProduct.company_id == company_id)
        )).all()

    scored = []
    for p in products:
        text = ' '.join(filter(None, [p.name, p.params or '', p.pack or '']))
        score = catalog_match_score(position, text)
        if score >= CATALOG_MATCH_THRESHOLD:
            scored.append((score, p))

    scored.sort(key=lambda x: -x[0])
    return [
        Offer(
            title=p.name,
            url='',
            domain='свой каталог',
            price=float(p.price) if p.price is not None else None,
            snippet=' '.join(filter(None, [p.params or '', p.price_text or ''])),
            source='catalog',
        )
        for _, p in scored[:limit]
    ]


async def _cache_get(key: str) -> Optional[List[Offer]]:
    async with DatabaseSession() as session:
        row = await session.get(ProductSearchCache, key)
        if not row:
            return None
        stamp = row.updated_at or row.created_at
        if stamp and stamp < datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS):
            return None
        row.hits = (row.hits or 0) + 1
        await session.commit()
        return [Offer(**o) for o in (row.results or [])]


async def _cache_put(key: str, text: str, offers: List[Offer]) -> None:
    """Пустую выдачу тоже кэшируем: «ничего не нашли» — такой же результат,
    и повторять бесплодный поиск при каждом прогоне незачем."""
    payload = [asdict(o) for o in offers]
    async with DatabaseSession() as session:
        row = await session.get(ProductSearchCache, key)
        now = datetime.utcnow()
        if row:
            row.results = payload
            row.updated_at = now
        else:
            session.add(ProductSearchCache(
                query_key=key, query_text=normalize_position(text),
                results=payload, hits=0, created_at=now, updated_at=now,
            ))
        await session.commit()


def build_query(position: str) -> str:
    """Поисковый запрос из позиции ТЗ.

    «купить» добавляем намеренно: без него в выдачу лезут сами тендеры и
    нормативные документы, а нам нужны магазины.
    """
    tokens = tokenize(position)[:10]
    return ' '.join(tokens) + ' купить цена' if tokens else position[:200]


async def find_offers(company_id: int, position: str,
                      limit: int = 5) -> PositionResult:
    """Полный цикл по одной позиции: каталог → кэш → веб."""
    result = PositionResult(position=position)

    if not tokenize(position):
        result.error = 'из позиции нечего извлечь'
        return result

    # 1. Свой каталог
    try:
        own = await match_catalog(company_id, position, limit=limit)
        if own:
            result.offers = own
            result.source = 'catalog'
            return result
    except Exception as e:
        logger.warning(f'Закупщик: каталог недоступен: {e}')

    key = cache_key(position)

    # 2. Кэш прошлых поисков
    try:
        cached = await _cache_get(key)
        if cached is not None:
            result.offers = cached[:limit]
            result.source = 'cache'
            return result
    except Exception as e:
        logger.warning(f'Закупщик: кэш недоступен: {e}')

    # 3. Общий веб-поиск
    from tender_sniper.search import yandex_search

    if not yandex_search.is_configured():
        result.error = ('Веб-поиск не настроен: нужны YANDEX_SEARCH_API_KEY '
                        'и YANDEX_SEARCH_FOLDER_ID')
        return result

    try:
        found = await yandex_search.search(build_query(position), limit=limit * 2)
    except Exception as e:
        logger.warning(f'Закупщик: поиск не удался для «{position[:50]}»: {e}')
        result.error = f'Поиск не удался: {e}'
        return result

    offers = [
        Offer(title=r.title, url=r.url, domain=r.domain,
              price=extract_price(r.snippet), snippet=r.snippet, source='web')
        for r in found
    ]
    # Варианты с распознанной ценой полезнее — поднимаем их наверх,
    # сохраняя порядок выдачи внутри каждой группы.
    offers.sort(key=lambda o: o.price is None)
    offers = offers[:limit]

    try:
        await _cache_put(key, position, offers)
    except Exception as e:
        logger.warning(f'Закупщик: не удалось сохранить кэш: {e}')

    result.offers = offers
    result.source = 'web' if offers else 'none'
    return result


async def run_search(card_id: int, company_id: int, by_user_id: int) -> None:
    """Фоновый подбор по всем позициям карточки.

    Состояние пишем в card.data, а не в словарь в памяти процесса: так оно
    переживает перезапуск воркера, и UI читает его тем же запросом, что и
    остальную карточку — отдельный реестр задач не нужен.
    """
    from database import PipelineCard, PipelineCardHistory

    async def _save(payload: Dict[str, Any]) -> None:
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return
            data = dict(card.data or {})
            data['buyer_search'] = payload
            card.data = data
            await session.commit()

    try:
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card or card.company_id != company_id:
                return
            card_data = dict(card.data or {})
            tender_name = card_data.get('name') or ''

        positions = extract_positions(card_data, tender_name)
        if not positions:
            await _save({'status': 'done', 'positions': [],
                         'error': 'Не удалось выделить позиции — запустите AI-анализ',
                         'updated_at': datetime.utcnow().isoformat()})
            return

        await _save({'status': 'running', 'positions': [],
                     'total': len(positions), 'done': 0,
                     'updated_at': datetime.utcnow().isoformat()})

        results: List[Dict[str, Any]] = []
        for idx, position in enumerate(positions, start=1):
            res = await find_offers(company_id, position)
            results.append(res.to_dict())
            # Пишем после каждой позиции: подбор долгий, и пользователь
            # должен видеть, что уже нашлось, а не пустой экран до конца.
            await _save({'status': 'running', 'positions': results,
                         'total': len(positions), 'done': idx,
                         'updated_at': datetime.utcnow().isoformat()})

        await _save({'status': 'done', 'positions': results,
                     'total': len(positions), 'done': len(positions),
                     'updated_at': datetime.utcnow().isoformat()})

        async with DatabaseSession() as session:
            session.add(PipelineCardHistory(
                card_id=card_id, user_id=by_user_id, action='buyer_search',
                payload={'positions': len(positions)},
            ))
            await session.commit()
        logger.info(f'Закупщик: карточка {card_id}, позиций {len(positions)}')
    except Exception as e:
        logger.error(f'Закупщик упал на карточке {card_id}: {e}', exc_info=True)
        try:
            await _save({'status': 'error', 'error': str(e)[:200],
                         'updated_at': datetime.utcnow().isoformat()})
        except Exception:
            pass


def extract_positions(card_data: Dict[str, Any], tender_name: str = '',
                      limit: int = 15) -> List[str]:
    """Позиции для поиска из разбора документации.

    Берём items_description из ai_analysis — это результат разбора ТЗ
    (см. _do_ai_enrich). Он приходит списком через «;» или нумерованным
    перечнем. Если разбора нет, ищем по названию тендера: хуже, но лучше,
    чем ничего.
    """
    analysis = (card_data or {}).get('ai_analysis') or {}
    items = ((analysis.get('fields') or {}).get('items_description') or '').strip()

    positions: List[str] = []
    if items and items.lower() not in ('не указано', 'нет данных'):
        # Нумерация вида «1. ... 2. ...» и разделители «;» / перевод строки.
        chunks = re.split(r';|\n|(?:(?<=\s)|^)\d{1,2}[.)]\s+', items)
        for chunk in chunks:
            text = chunk.strip(' \t\r-*•·')
            if 5 <= len(text) <= 300:
                positions.append(text)

    if not positions and tender_name:
        positions = [tender_name]

    seen, unique = set(), []
    for p in positions:
        key = normalize_position(p)
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
        if len(unique) >= limit:
            break
    return unique
