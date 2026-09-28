"""Начальные формы слов для матчинга и объяснения совпадений.

Общее место для smart_matcher и match_context: «проектора» и «проектор» —
одно слово, «проектов» и «проектор» — разные, и решаться это в двух
местах по-разному не должно.

Не путать с `tender_sniper/morphology.py` — тот написан под pymorphy2,
которого в зависимостях нет, и никем не импортируется.
"""
import functools
import logging
import re

logger = logging.getLogger(__name__)

WORDS_RE = re.compile(r'[а-яёa-z0-9]{3,}', re.IGNORECASE)

_MORPH = None


def _analyzer():
    """Словари грузятся около секунды, поэтому лениво и один раз."""
    global _MORPH
    if _MORPH is None:
        import pymorphy3
        _MORPH = pymorphy3.MorphAnalyzer()
    return _MORPH


@functools.lru_cache(maxsize=100_000)
def lemma(word: str) -> str:
    """Начальная форма слова. Латиница и цифры возвращаются как есть."""
    word = word.lower()
    if not any('а' <= c <= 'я' for c in word):
        return word
    try:
        return _analyzer().parse(word)[0].normal_form
    except Exception as e:  # pragma: no cover — словари недоступны
        logger.warning("Морфология недоступна (%s), слово как есть: %s", e, word)
        return word


@functools.lru_cache(maxsize=512)
def text_lemmas(text: str) -> frozenset:
    """Начальные формы всех слов текста.

    Кэш по тексту обязателен: у крупного фильтра полторы тысячи
    ключевиков, и без него разбор одного и того же извещения повторялся бы
    столько же раз.
    """
    return frozenset(lemma(w) for w in WORDS_RE.findall(text.lower()))
