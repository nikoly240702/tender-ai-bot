"""
Единый резолвер названия тендера.

ОДНА точка правды для названия, которое видит пользователь — в Telegram-карточке,
в кабинете (Pipeline) и в Bitrix24. Цель: название строго отражает ПРЕДМЕТ
закупки («Закупка бумаги», «Поставка насоса»), а не тип процедуры
(«Запрос котировок в электронной форме»).

Детерминированно и без галлюцинаций: берём реальные данные карточки тендера
в порядке надёжности, тип процедуры/бюрократию никогда не отдаём как название.
"""

import re
import html
import logging
from typing import Dict, Any, Optional

from tender_sniper.procedure_titles import is_procedure_type_only

logger = logging.getLogger(__name__)


_JUNK_NAME_PATTERNS = [
    re.compile(r'^[\d\W_]+$'),
    re.compile(r'^\d{4}-\d{3,}'),
    re.compile(r'^№?\s*[\d\.\-/]+\s*$'),
    re.compile(r'^электронн\w*\s+формуляр', re.I),
    re.compile(r'^формуляр\b', re.I),
    re.compile(r'^извещени\w*\s+о\s+(закупке|проведении)', re.I),
    re.compile(r'^уведомление\b', re.I),
    re.compile(r'^(ФГБОУ|ГБОУ|ГАУЗ|ГБУ|МБУ|МКУ|ФГУП|ГУП|МУП|ФКУ|ФГБУ|АО|ООО|ПАО|ИП|ОГБУЗ|ГБУЗ|КГБУЗ)\b', re.I),
    re.compile(r'^(ГОСУДАРСТВЕН|МУНИЦИПАЛЬ|ФЕДЕРАЛЬН|КОМИТЕТ|ДЕПАРТАМЕНТ|МИНИСТЕРСТВ|АДМИНИСТРАЦ|УПРАВЛЕНИ[ЕЯ]|КАЗЕНН)', re.I),
]

# Реальный предмет закупки в summary (с HTML-тегами или без)
_OBJECT_PATTERNS = [
    re.compile(r'Наименование объекта закупки:\s*(?:</strong>)?\s*([^<\n]+)', re.I),
    re.compile(r'Объект закупки:\s*(?:</strong>)?\s*([^<\n]+)', re.I),
    re.compile(r'Предмет (?:контракта|закупки):\s*(?:</strong>)?\s*([^<\n]+)', re.I),
    re.compile(r'Наименование закупки:\s*(?:</strong>)?\s*([^<\n]+)', re.I),
]

_BUREAU_PHRASES = (
    'в соответствии с', 'осуществляемая в соответствии',
    'статьи 93', 'закона № 44', 'закона №44', 'частью 12',
)


def looks_like_junk_name(name: str) -> bool:
    """True, если строка не годится как название тендера: пустая, слишком
    короткая, только тип процедуры, номер/бюрократия/название организации."""
    if not name:
        return True
    t = name.strip()
    if len(t) < 15:
        return True
    if is_procedure_type_only(t):
        return True
    return any(p.search(t) for p in _JUNK_NAME_PATTERNS)


def _clean_text(s: str) -> str:
    s = re.sub(r'<[^>]+>', ' ', s or '')
    return re.sub(r'\s+', ' ', s).strip()


def _trim_tail_clause(s: str) -> str:
    """Убирает хвост «… в рамках запроса котировок/аукциона …» — это про
    процедуру, а не про предмет."""
    s = re.sub(r'\s+в рамках\s+(?:запроса|проведения|аукциона|конкурса).*$', '', s, flags=re.I)
    return s.strip(' .;:،,')


# Слова, на которые название закончиться не может: после предлога или
# союза обязано идти продолжение.
_DANGLING_WORDS = {
    'по', 'для', 'на', 'в', 'из', 'с', 'со', 'при', 'от', 'до', 'к', 'о',
    'об', 'у', 'за', 'под', 'над', 'без', 'через', 'между', 'и', 'или', 'а',
}
# Окончания прилагательных: «по капитальному», «работ по текущему».
_ADJECTIVE_TAIL = re.compile(
    r'(ому|ему|ого|его|ым|им|ой|ей|ая|яя|ое|ее|ые|ие|ый|ий|ых|их)$', re.I)


def looks_truncated(name: str, original: str = '') -> bool:
    """Оборвано ли название на полуслове.

    Появляется, когда модель понимает «сократи до 3-5 слов» буквально и
    отдаёт первые слова оригинала: «Выполнение работ по капитальному».
    Отличить обрыв от нормального сокращения помогает оригинал — «Поставка
    перчаток» вместо «Поставка перчаток для нужд больницы» обрывом не
    является, потому что кончается существительным.
    """
    text = (name or '').strip()
    if not text:
        return True
    if text[-1] in '(«,-—:;/+':
        return True

    words = re.findall(r'[^\s]+', text)
    if not words:
        return True
    last = words[-1].strip('.,;:!?»)').lower()
    if last in _DANGLING_WORDS:
        return True

    # Прилагательное в конце законно в обратном порядке слов («перчаток
    # хирургических»), но не после предлога: «работ по капитальному» — это
    # обрыв перед существительным. Второй признак обрыва — оригинал
    # продолжается ровно с того места, где кончилось название. Третий —
    # прилагательному не с чем согласовываться: «Поставка
    # водонепроницаемых» против «Поставка бахил водонепроницаемых».
    if _ADJECTIVE_TAIL.search(last):
        prev = words[-2].strip('.,;:!?»)(').lower() if len(words) > 1 else ''
        if prev in _DANGLING_WORDS:
            return True
        src = _clean_text(original).lower()
        cur = _clean_text(text).lower()
        if src and src.startswith(cur) and len(src) > len(cur):
            return True
        if _adjective_hangs_alone(last, prev):
            return True

    return False


_MORPH = None


def _morph_analyzer():
    """Морфология подключается лениво: словари грузятся около секунды, а
    нужны только для разбора хвоста названия."""
    global _MORPH
    if _MORPH is None:
        import pymorphy3
        _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


def _adjective_hangs_alone(adjective: str, previous: str) -> bool:
    """Прилагательное в конце названия не согласовано с соседом слева.

    «Поставка водонепроницаемых»: «поставка» — именительный падеж
    единственного числа, «водонепроницаемых» — родительный множественного,
    согласования нет, значит определяемое слово отрезано. В «Поставка
    бахил водонепроницаемых» сосед слева — «бахил», и падеж с числом
    совпадают.
    """
    if not previous:
        return False
    try:
        morph = _morph_analyzer()
        adj_parses = [p for p in morph.parse(adjective) if 'ADJF' in p.tag]
        if not adj_parses:
            return False
        prev_parses = [p for p in morph.parse(previous) if 'NOUN' in p.tag]
        if not prev_parses:
            return False
        for adj in adj_parses:
            for noun in prev_parses:
                if adj.tag.case == noun.tag.case and adj.tag.number == noun.tag.number:
                    return False
        return True
    except Exception as e:  # pragma: no cover — словари недоступны
        logger.warning("Морфология недоступна (%s), хвост не проверяю", e)
        return False


# Вводные обороты сводки: «Тендер на поставку…», «Закупка на выполнение…».
_SUMMARY_LEAD_IN = re.compile(
    r'^\s*(?:тендер|закупка|аукцион|процедура|контракт)\s+(?:на|по)\s+', re.I)

# После вводного оборота существительное стоит в винительном или дательном
# падеже («на поставку», «по ремонту»). Морфологического разбора здесь нет
# намеренно: список закрытый, из реальных формулировок сводок, а на
# незнакомом слове вводный оборот просто остаётся на месте — «Закупка на
# техническое обслуживание» читается нормально и грамматически верно.
_HEAD_NOMINATIVE = {
    'поставку': 'поставка', 'закупку': 'закупка', 'покупку': 'покупка',
    'услугу': 'услуга', 'услуги': 'услуги', 'аренду': 'аренда',
    'разработку': 'разработка', 'установку': 'установка', 'замену': 'замена',
    'модернизацию': 'модернизация', 'реконструкцию': 'реконструкция',
    'ремонт': 'ремонт', 'ремонту': 'ремонт', 'монтаж': 'монтаж',
    'монтажу': 'монтаж', 'вывоз': 'вывоз', 'вывозу': 'вывоз',
    # Средний род в этих падежах не меняется.
    'выполнение': 'выполнение', 'оказание': 'оказание', 'оснащение': 'оснащение',
    'обслуживание': 'обслуживание', 'приобретение': 'приобретение',
    'строительство': 'строительство', 'изготовление': 'изготовление',
}
# Хвосты про деньги и сроки — это не предмет закупки.
_SUMMARY_TAIL = re.compile(
    r'\s+(?:с\s+начальной\s+ценой|на\s+сумму|стоимостью|с\s+НМЦК|со\s+сроком|'
    r'с\s+датой|общей\s+стоимостью)\b.*$', re.I)


def subject_from_summary(summary: str) -> Optional[str]:
    """Достаёт предмет закупки из AI-сводки.

    Сводка приходит в том же ответе модели, что и краткое имя, и в отличие
    от него описывает суть целиком: «Тендер на выполнение капитального
    ремонта помещений с начальной ценой до 3 миллионов рублей».
    """
    text = _clean_text(summary or '')
    if not text:
        return None

    lead = _SUMMARY_LEAD_IN.match(text)
    if lead:
        rest = text[lead.end():]
        head = re.match(r'([А-Яа-яЁёA-Za-z-]+)', rest)
        nominative = _HEAD_NOMINATIVE.get(head.group(1).lower()) if head else None
        if nominative:
            text = nominative + rest[head.end():]

    text = _SUMMARY_TAIL.sub('', text)
    text = _trim_tail_clause(text).strip(' .;:,')
    if len(text) < 10:
        return None

    # Первая фраза сводки — предмет; остальное обычно пояснения.
    text = re.split(r'(?<=[а-яё])\.\s+[А-ЯЁ]', text)[0].strip(' .;:,')
    return text[:1].upper() + text[1:] if text else None


def extract_object_from_summary(summary: str) -> Optional[str]:
    """Детерминированно достаёт реальный предмет закупки из summary тендера."""
    if not summary:
        return None
    for pat in _OBJECT_PATTERNS:
        m = pat.search(summary)
        if not m:
            continue
        text = html.unescape(re.sub(r'\s+', ' ', m.group(1)).strip())
        low = text.lower()
        if len(text) < 8 or is_procedure_type_only(text):
            continue
        if any(p in low for p in _BUREAU_PHRASES):
            continue
        return text
    return None


def resolve_tender_name(
    tender: Dict[str, Any],
    match_info: Optional[Dict[str, Any]] = None,
    max_length: int = 200,
) -> str:
    """Возвращает название, строго отражающее ПРЕДМЕТ закупки.

    Приоритет (от самого надёжного и не-галлюцинирующего):
      1. сырое имя тендера, если оно осмысленное (для большинства тендеров —
         это и есть нормальное «Поставка ...»);
      2. реальный «Наименование объекта закупки» из summary;
      3. короткое AI-название (ai_simple_name), если оно не пустышка и не
         оборвано на полуслове;
      4. предмет закупки из AI-сводки, затем причина совпадения;
      5. описание тендера;
      6. честный фолбэк на номер — но НИКОГДА не сырой тип процедуры.

    На любом шаге оборванное название пропускается: лучше показать номер
    тендера, чем «Выполнение работ по капитальному».
    """
    match_info = match_info or {}

    name = (tender.get('name') or '').strip()
    if name and not looks_like_junk_name(name) and not looks_truncated(name):
        return name[:max_length]

    obj = extract_object_from_summary(tender.get('summary') or '')
    if obj and not looks_truncated(obj):
        return obj[:max_length]

    ai_simple = _clean_text(match_info.get('ai_simple_name') or '')
    if (ai_simple and not looks_like_junk_name(ai_simple)
            and not looks_truncated(ai_simple, original=name)):
        return ai_simple[:max_length]

    subject = subject_from_summary(match_info.get('ai_summary') or '')
    if subject and not looks_like_junk_name(subject) and not looks_truncated(subject):
        return subject[:max_length]

    for key in ('ai_summary', 'ai_reason'):
        val = _trim_tail_clause(_clean_text(match_info.get(key) or ''))
        if len(val) >= 10 and not looks_like_junk_name(val) and not looks_truncated(val):
            return val[:120]

    for alt_key in ('summary', 'description', 'tender_name'):
        alt = _clean_text(tender.get(alt_key) or '')
        if alt and not looks_like_junk_name(alt):
            return alt[:max_length]

    return 'Тендер №' + (tender.get('number') or '—')
