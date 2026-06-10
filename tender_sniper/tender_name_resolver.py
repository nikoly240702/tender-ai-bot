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
from typing import Dict, Any, Optional

from tender_sniper.procedure_titles import is_procedure_type_only


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
      3. короткое AI-название (ai_simple_name), если оно не пустышка;
      4. AI-сводка / причина (основаны на документации тендера);
      5. описание тендера;
      6. честный фолбэк на номер — но НИКОГДА не сырой тип процедуры.
    """
    match_info = match_info or {}

    name = (tender.get('name') or '').strip()
    if name and not looks_like_junk_name(name):
        return name[:max_length]

    obj = extract_object_from_summary(tender.get('summary') or '')
    if obj:
        return obj[:max_length]

    ai_simple = _clean_text(match_info.get('ai_simple_name') or '')
    if ai_simple and not looks_like_junk_name(ai_simple):
        return ai_simple[:max_length]

    for key in ('ai_summary', 'ai_reason'):
        val = _trim_tail_clause(_clean_text(match_info.get(key) or ''))
        if len(val) >= 10 and not looks_like_junk_name(val):
            return val[:120]

    for alt_key in ('summary', 'description', 'tender_name'):
        alt = _clean_text(tender.get(alt_key) or '')
        if alt and not looks_like_junk_name(alt):
            return alt[:max_length]

    return 'Тендер №' + (tender.get('number') or '—')
