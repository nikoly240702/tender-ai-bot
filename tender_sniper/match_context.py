"""Почему тендер попал в подборку.

8% уведомлений приходят с настоящим, но зонтичным именем — «Поставка
оборудования», «Медицинские расходные материалы». Ключевое слово совпало
внутри спецификации, в шапке его не видно, и уведомление выглядит
мусором, хотя тендер подходящий (диагноз 17.07.2026, замер 28.09.2026).

Поэтому карточка показывает кусок описания вокруг совпавшего слова — но
только тогда, когда в названии слова нет: иначе строка дублирует шапку.
"""
import re
from typing import Iterable, Optional

from tender_sniper.matching.lemmas import WORDS_RE, lemma, text_lemmas

# Пометки матчера: «Совпадение: 📌 бумага офисная», «проектор (частичное)»,
# «канцелярия (синоним: офис)».
_MARKERS = re.compile(r'^\s*совпадение:\s*|\s*\((?:частичное|синоним:[^)]*)\)\s*$', re.I)
_HTML = re.compile(r'<[^>]+>')

SNIPPET_WIDTH = 120


def clean_keyword(keyword: str) -> str:
    """Убирает служебные пометки, оставляя само ключевое слово."""
    text = _MARKERS.sub('', str(keyword or ''))
    return text.replace('📌', '').strip(' .,;:')


def _contains(word_lemmas: Iterable[str], text: str) -> bool:
    present = text_lemmas(text)
    return all(w in present for w in word_lemmas)


def match_snippet(keywords: Iterable[str], name: str,
                  description: str, width: int = SNIPPET_WIDTH) -> Optional[str]:
    """Кусок описания вокруг совпавшего слова — или None, если объяснять нечего.

    None значит одно из двух: слово и так видно в названии, либо в описании
    его нет (совпадение было по синониму или по другому полю).
    """
    text = re.sub(r'\s+', ' ', _HTML.sub(' ', description or '')).strip()
    if not text:
        return None

    for raw in keywords or []:
        keyword = clean_keyword(raw)
        if not keyword:
            continue
        key_lemmas = [lemma(w) for w in WORDS_RE.findall(keyword.lower())]
        if not key_lemmas:
            continue
        # В названии видно и так — вторая строка про то же только мешает.
        if _contains(key_lemmas, name or ''):
            return None
        if not _contains(key_lemmas, text):
            continue

        first = key_lemmas[0]
        for word in WORDS_RE.finditer(text):
            if lemma(word.group(0)) != first:
                continue
            start = max(0, word.start() - width // 2)
            end = min(len(text), word.end() + width // 2)
            snippet = text[start:end].strip()
            if start > 0:
                snippet = '…' + snippet
            if end < len(text):
                snippet = snippet + '…'
            return snippet

    return None
