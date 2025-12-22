"""
Russian Morphology Analyzer.

Генерация словоформ и лемматизация русского языка с использованием pymorphy2.
Улучшает качество поиска за счёт понимания морфологии.

Feature flag: morphology (config/features.yaml)
"""

import logging
from typing import List, Set, Optional
from functools import lru_cache

logger = logging.getLogger(__name__)

# Try to import pymorphy2
try:
    import pymorphy2
    PYMORPHY2_AVAILABLE = True
except ImportError:
    PYMORPHY2_AVAILABLE = False
    logger.warning("⚠️ pymorphy2 not installed. Morphology features will be limited.")


class MorphologyAnalyzer:
    """
    Russian morphology analyzer using pymorphy2.

    Features:
        - Word form generation (все падежи, числа)
        - Lemmatization (нормальная форма)
        - Keyword expansion
        - Text normalization
    """

    def __init__(self):
        """Initialize morphology analyzer."""
        self.morph = None
        self._available = False

        if PYMORPHY2_AVAILABLE:
            try:
                self.morph = pymorphy2.MorphAnalyzer()
                self._available = True
                logger.info("✅ Morphology analyzer initialized with pymorphy2")
            except Exception as e:
                logger.error(f"❌ Failed to initialize pymorphy2: {e}")
        else:
            logger.info("ℹ️ Morphology running in fallback mode (no pymorphy2)")

    @property
    def is_available(self) -> bool:
        """Check if morphology analyzer is available."""
        return self._available

    @lru_cache(maxsize=10000)
    def get_word_forms(self, word: str) -> Set[str]:
        """
        Generate all word forms (падежи, числа).

        Example:
            >>> get_word_forms("компьютер")
            {'компьютер', 'компьютера', 'компьютеру', 'компьютером',
             'компьютере', 'компьютеры', 'компьютеров', ...}

        Args:
            word: Russian word

        Returns:
            Set of all word forms
        """
        if not self._available:
            return {word, word.lower()}

        try:
            parsed = self.morph.parse(word)[0]
            forms = set()

            for form in parsed.lexeme:
                forms.add(form.word)
                forms.add(form.word.lower())

            # Also add original
            forms.add(word)
            forms.add(word.lower())

            return forms
        except Exception as e:
            logger.warning(f"Error generating word forms for '{word}': {e}")
            return {word, word.lower()}

    @lru_cache(maxsize=10000)
    def get_normal_form(self, word: str) -> str:
        """
        Get normal (lemma) form of a word.

        Example:
            >>> get_normal_form("компьютерами")
            'компьютер'
            >>> get_normal_form("серверов")
            'сервер'

        Args:
            word: Russian word

        Returns:
            Lemma (normal form)
        """
        if not self._available:
            return word.lower()

        try:
            parsed = self.morph.parse(word)[0]
            return parsed.normal_form
        except Exception as e:
            logger.warning(f"Error getting normal form for '{word}': {e}")
            return word.lower()

    def expand_keywords(self, keywords: List[str], max_forms_per_word: int = 15) -> List[str]:
        """
        Expand keywords with word forms.

        Args:
            keywords: List of keywords
            max_forms_per_word: Maximum forms to generate per word (to avoid explosion)

        Returns:
            Expanded list of keywords
        """
        expanded = set()

        for keyword in keywords:
            # Add original
            expanded.add(keyword)
            expanded.add(keyword.lower())

            # Split multi-word keywords
            words = keyword.split()

            if len(words) == 1:
                # Single word - expand forms
                forms = self.get_word_forms(keyword)
                # Limit forms to avoid explosion
                limited_forms = list(forms)[:max_forms_per_word]
                expanded.update(limited_forms)
            else:
                # Multi-word phrase - expand each word and recombine
                # Keep original phrase
                expanded.add(keyword)

                # Add normalized version
                normalized = [self.get_normal_form(w) for w in words]
                expanded.add(' '.join(normalized))

        return list(expanded)

    def normalize_text(self, text: str) -> str:
        """
        Normalize text to lemmas.

        Args:
            text: Russian text

        Returns:
            Text with all words replaced by their lemmas
        """
        if not text:
            return ""

        words = text.lower().split()
        normalized = [self.get_normal_form(w) for w in words]
        return ' '.join(normalized)

    def get_word_info(self, word: str) -> Optional[dict]:
        """
        Get detailed morphological information about a word.

        Args:
            word: Russian word

        Returns:
            Dictionary with word info or None
        """
        if not self._available:
            return None

        try:
            parsed = self.morph.parse(word)[0]
            return {
                'word': word,
                'normal_form': parsed.normal_form,
                'pos': parsed.tag.POS,  # Part of speech
                'score': parsed.score,
                'forms_count': len(parsed.lexeme),
            }
        except Exception as e:
            logger.warning(f"Error getting word info for '{word}': {e}")
            return None

    def is_similar(self, word1: str, word2: str) -> bool:
        """
        Check if two words are morphologically similar (same lemma).

        Args:
            word1: First word
            word2: Second word

        Returns:
            True if words have the same lemma
        """
        return self.get_normal_form(word1) == self.get_normal_form(word2)

    def clear_cache(self):
        """Clear the LRU cache."""
        self.get_word_forms.cache_clear()
        self.get_normal_form.cache_clear()
        logger.info("Morphology cache cleared")

    def get_cache_info(self) -> dict:
        """Get cache statistics."""
        return {
            'word_forms': self.get_word_forms.cache_info()._asdict(),
            'normal_form': self.get_normal_form.cache_info()._asdict(),
        }


# Singleton instance
morphology = MorphologyAnalyzer()


# Convenience functions
def get_word_forms(word: str) -> Set[str]:
    """Get all word forms for a word."""
    return morphology.get_word_forms(word)


def get_normal_form(word: str) -> str:
    """Get normal (lemma) form of a word."""
    return morphology.get_normal_form(word)


def expand_keywords(keywords: List[str]) -> List[str]:
    """Expand keywords with word forms."""
    return morphology.expand_keywords(keywords)


def normalize_text(text: str) -> str:
    """Normalize text to lemmas."""
    return morphology.normalize_text(text)


def is_morphology_available() -> bool:
    """Check if morphology is available."""
    return morphology.is_available
