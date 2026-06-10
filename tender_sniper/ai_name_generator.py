"""
AI Name Generator для тендеров.

Генерирует короткие, понятные названия для тендеров вместо длинных юридических текстов.
Использует LLM и кэширует результаты для экономии API запросов.
"""

import os
import re
import sys
import html
import logging
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

from tender_sniper.procedure_titles import is_procedure_type_only

try:
    from src.analyzers.llm_adapter import LLMFactory
except ImportError:
    # Fallback для разных структур проекта
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "llm_adapter",
        Path(__file__).parent.parent / "src" / "analyzers" / "llm_adapter.py"
    )
    llm_adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(llm_adapter)
    LLMFactory = llm_adapter.LLMFactory

logger = logging.getLogger(__name__)


class TenderNameGenerator:
    """
    AI-генератор коротких названий для тендеров.

    Особенности:
    - Использует LLM для генерации понятных названий
    - Кэширует результаты в памяти и БД
    - Автоматический fallback на оригинальное название при ошибках
    - Поддержка различных LLM провайдеров
    """

    def __init__(
        self,
        llm_provider: str = None,
        llm_api_key: str = None,
        llm_model: str = None,
        cache_enabled: bool = True
    ):
        """
        Инициализация генератора названий.

        Args:
            llm_provider: LLM провайдер ('groq', 'openai', 'anthropic', и т.д.)
                         По умолчанию читает из env LLM_PROVIDER
            llm_api_key: API ключ для LLM
                        По умолчанию читает из соответствующей env переменной
            llm_model: Название модели (опционально)
            cache_enabled: Включить кэширование (по умолчанию True)
        """
        # Определяем провайдера и ключ
        self.provider = llm_provider or os.getenv('LLM_PROVIDER', 'groq')

        # Получаем API ключ в зависимости от провайдера
        if not llm_api_key:
            if self.provider == 'anthropic':
                llm_api_key = os.getenv('ANTHROPIC_API_KEY')
            elif self.provider == 'openai':
                llm_api_key = os.getenv('OPENAI_API_KEY')
            elif self.provider == 'groq':
                llm_api_key = os.getenv('GROQ_API_KEY')
            elif self.provider == 'gemini':
                llm_api_key = os.getenv('GEMINI_API_KEY')

        self.api_key = llm_api_key
        self.model = llm_model
        self.cache_enabled = cache_enabled

        # In-memory кэш для быстрого доступа
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl = timedelta(days=30)  # TTL для кэша

        # Создаем LLM адаптер
        try:
            self.llm = LLMFactory.create(
                provider=self.provider,
                api_key=self.api_key,
                model=self.model,
                max_tokens=30,  # 3-5 слов — достаточно 30 токенов
                temperature=0.1  # Очень низкая для стабильных коротких ответов
            )
            logger.info(f"✅ AI Name Generator инициализирован с {self.provider}")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось инициализировать LLM: {e}. AI-генерация отключена.")
            self.llm = None

    def _get_cache_key(self, original_name: str) -> str:
        """Генерирует ключ кэша на основе оригинального названия."""
        return hashlib.md5(original_name.encode('utf-8')).hexdigest()

    def _get_from_memory_cache(self, cache_key: str) -> Optional[str]:
        """Получает название из in-memory кэша."""
        if not self.cache_enabled:
            return None

        cached = self._memory_cache.get(cache_key)
        if not cached:
            return None

        # Проверяем TTL
        if datetime.now() - cached['timestamp'] > self._cache_ttl:
            # Кэш устарел
            del self._memory_cache[cache_key]
            return None

        return cached['name']

    def _save_to_memory_cache(self, cache_key: str, generated_name: str):
        """Сохраняет название в in-memory кэш."""
        if not self.cache_enabled:
            return

        self._memory_cache[cache_key] = {
            'name': generated_name,
            'timestamp': datetime.now()
        }

    _JUNK_RE = re.compile(
        r'^('
        r'ФГБОУ|ГБОУ|ГАУЗ|ГБУ|МБУ|МКУ|ФГУП|ГУП|МУП|ФКУ|ФГБУ|АО|ООО|ПАО|ИП|ОГБУЗ|ГБУЗ|КГБУЗ'
        r'|ГОСУДАРСТВЕН|МУНИЦИПАЛЬ|ФЕДЕРАЛЬН|КОМИТЕТ|ДЕПАРТАМЕНТ|МИНИСТЕРСТВ|АДМИНИСТРАЦ|УПРАВЛЕНИ[ЕЯ]|КАЗЕНН'
        r'|ЭЛЕКТРОНН\w*\s+ФОРМУЛЯР'
        r'|ФОРМУЛЯР'
        r'|ИЗВЕЩЕНИ\w*\s+О\s+(ЗАКУПКЕ|ПРОВЕДЕНИИ)'
        r'|УВЕДОМЛЕНИЕ'
        r')',
        re.I,
    )

    _PROCEDURE_PREFIXES = [
        'конкурс с ограниченным участием',
        'электронный аукцион',
        'запрос предложений',
        'открытый конкурс',
        'запрос котировок',
        'аукцион',
        'закупка',
        'конкурс',
    ]

    # "в электронной форме" / "в эл. форме" — это часть НАЗВАНИЯ ТИПА процедуры,
    # а не предмет закупки. Никогда не должно становиться названием тендера.
    _FORM_SUFFIX_RE = re.compile(r'^в\s+эл(?:ектронн\w+|\.?)\s+форме\b', re.I)

    def _is_procedure_type_only(self, name: str) -> bool:
        """True, если название — ТОЛЬКО тип процедуры (способ определения
        поставщика) без указания предмета закупки.
        Примеры: «Запрос котировок в электронной форме», «Электронный аукцион»."""
        return is_procedure_type_only(name)

    def _strip_procedure_prefix(self, name: str) -> str:
        """
        Вырезает мусорный префикс типа 'Электронный аукцион №...' из начала.
        Возвращает оставшуюся часть, если она осмысленная (≥10 символов),
        иначе возвращает исходное название.
        """
        import re

        # Строка целиком — это тип процедуры без предмета: не выдумываем «огрызок»,
        # возвращаем как есть (предмет подставит summary / _is_only_procedure_number).
        if self._is_procedure_type_only(name):
            return name

        lower = name.lower().strip()

        for prefix in self._PROCEDURE_PREFIXES:
            if not lower.startswith(prefix):
                continue

            # Вырезаем: тип процедуры + необязательный «в электронной форме» + №/номер
            tail = name[len(prefix):].strip()
            # «в электронной форме» — часть типа процедуры, а не предмет
            tail = self._FORM_SUFFIX_RE.sub('', tail).strip()
            # Убираем только реальный номер «№XXXX» / «#XXXX» (начинается с цифры),
            # не съедая первое осмысленное слово предмета закупки
            tail = re.sub(r'^[№#]?\s*\d[\w\d/\-]*', '', tail).strip()
            # Убираем лидирующие разделители (- / : — –)
            tail = re.sub(r'^[\-–—:/,\.]+', '', tail).strip()
            # Ещё раз «в электронной форме», если он стоял после номера
            tail = self._FORM_SUFFIX_RE.sub('', tail).strip()

            if len(tail) >= 10:
                # Первую букву делаем заглавной
                return tail[0].upper() + tail[1:]

            return name  # после очистки осталась пустота → это чистый тип процедуры

        return name  # Не нашли паттерн — возвращаем оригинал

    # Источники реального предмета закупки в summary (приоритет — сверху вниз)
    _OBJECT_PATTERNS = [
        r'Наименование объекта закупки:\s*</strong>\s*([^<]+)',
        r'<strong>\s*Объект закупки:\s*</strong>\s*([^<]+)',
        r'Объект закупки:\s*</strong>\s*([^<]+)',
        r'<strong>\s*Предмет (?:контракта|закупки):\s*</strong>\s*([^<]+)',
        r'Наименование закупки:\s*</strong>\s*([^<]+)',
        r'<strong>\s*Краткое описание:\s*</strong>\s*([^<]+)',
        r'Наименование товара[^:<]*:\s*</strong>\s*([^<]+)',
    ]

    _BUREAU_PHRASES = (
        'в соответствии с', 'осуществляемая в соответствии',
        'статьи 93', 'закона № 44', 'закона №44', 'частью 12',
    )

    def _extract_object_from_summary(
        self, tender_data: Optional[Dict[str, Any]]
    ) -> Optional[str]:
        """Достаёт реальный предмет закупки из summary тендера.
        Возвращает None, если предмет не найден или сам является мусором
        (тип процедуры / бюрократия). Это детерминированно и не галлюцинирует."""
        if not tender_data:
            return None
        summary = tender_data.get('summary') or ''
        if not summary:
            return None

        for pattern in self._OBJECT_PATTERNS:
            m = re.search(pattern, summary, re.IGNORECASE)
            if not m:
                continue
            text = html.unescape(re.sub(r'\s+', ' ', m.group(1)).strip())
            low = text.lower()
            if len(text) < 8:
                continue
            if any(p in low for p in self._BUREAU_PHRASES):
                continue
            if self._is_procedure_type_only(text):
                continue
            return text
        return None

    def _is_only_procedure_number(self, name: str) -> bool:
        """True, если название — ТОЛЬКО тип процедуры + номер, без описания."""
        stripped = self._strip_procedure_prefix(name)
        # Если после очистки ничего не осталось — это чисто мусорное название
        return stripped == name and any(
            name.lower().strip().startswith(p) for p in self._PROCEDURE_PREFIXES
        ) and len(name) < 60  # Длинные — скорее всего содержат описание

    def generate_short_name(
        self,
        original_name: str,
        tender_data: Optional[Dict[str, Any]] = None,
        max_length: int = 80
    ) -> str:
        """
        Генерирует короткое понятное название для тендера.

        Args:
            original_name: Оригинальное юридическое название тендера
            tender_data: Дополнительные данные тендера (опционально)
            max_length: Максимальная длина сгенерированного названия

        Returns:
            Короткое AI-сгенерированное название или оригинальное при ошибке
        """
        if not original_name or not original_name.strip():
            return "Без названия"

        original_name = re.sub(r'^[А-ЯA-Z]{1,4}[\-_]\d{2,6}[_\s]+', '', original_name).strip()

        is_garbage = (
            self._is_only_procedure_number(original_name)
            or self._is_procedure_type_only(original_name)
        )

        # Если название бесполезное (только тип процедуры / бюрократия),
        # детерминированно подставляем реальный предмет закупки из summary.
        # Это убирает названия вида «Запрос котировок в электронной форме»
        # и при этом не галлюцинирует — берём ровно то, что в карточке тендера.
        if is_garbage or self._JUNK_RE.search(original_name.strip()):
            real_object = self._extract_object_from_summary(tender_data)
            if real_object:
                original_name = real_object
                is_garbage = self._is_only_procedure_number(original_name) or \
                    self._is_procedure_type_only(original_name)

        cleaned = self._strip_procedure_prefix(original_name)

        # Если после очистки префикса стало короче — используем очищенное
        if cleaned != original_name:
            original_name = cleaned

        is_org_name = bool(self._JUNK_RE.search(original_name.strip()))

        if not is_garbage and not is_org_name and len(original_name) <= max_length:
            return original_name

        # Проверяем кэш
        cache_key = self._get_cache_key(original_name)
        cached_name = self._get_from_memory_cache(cache_key)
        if cached_name:
            logger.debug(f"💾 Название найдено в кэше")
            return cached_name

        # Если LLM недоступен, возвращаем fallback
        if not self.llm:
            logger.debug("⚠️ LLM недоступен, используем fallback")
            return self._fallback_short_name(original_name, max_length)

        # Генерируем через LLM
        try:
            logger.info(f"🤖 Генерация короткого названия через {self.provider}...")

            system_prompt = """Ты сокращаешь названия тендеров до 3-5 слов.

ГЛАВНОЕ ПРАВИЛО: используй ТОЛЬКО слова из оригинального названия. НИКОГДА не придумывай новые слова, не интерпретируй, не фантазируй. Если название уже короткое — верни как есть.

УБИРАЙ: тип процедуры, номера, годы, юридические ссылки, "для нужд", "в целях обеспечения", названия организаций.

Примеры:
"Электронный аукцион №0372200015125000456 на поставку компьютерного оборудования для нужд ГБОУ" → "Компьютерное оборудование"
"Запрос котировок лот 3 Р-45 оказание услуг по уборке помещений 2025" → "Уборка помещений"
"Поставка оконных блоков" → "Поставка оконных блоков"
"Поставка медицинских изделий (перчатки латексные) для ГАУЗ" → "Медицинские перчатки"
"Капитальный ремонт фасада здания административного корпуса" → "Капремонт фасада здания"

ЗАПРЕЩЕНО: "Поставка оконных блоков" → "Робот-мойщик окон" (это ВЫДУМКА, слов "робот" и "мойщик" нет в оригинале!)

Отвечай ТОЛЬКО названием (3-5 слов), без пояснений и кавычек."""

            user_prompt = f"""Тендер:
{original_name}

Дай название в 3-5 слов."""

            # Добавляем контекст из tender_data если есть
            if tender_data:
                import re as _re
                # summary содержит "Наименование объекта закупки" — ключевой источник для мусорных имён
                summary = tender_data.get('summary', '')
                if summary:
                    clean_summary = _re.sub(r'<[^>]+>', ' ', summary)
                    clean_summary = _re.sub(r'\s+', ' ', clean_summary).strip()[:600]
                    user_prompt += f"\n\nОписание тендера:\n{clean_summary}"
                customer = tender_data.get('customer_name') or tender_data.get('customer', '')
                region = tender_data.get('region') or tender_data.get('customer_region', '')
                if customer:
                    user_prompt += f"\n\nЗаказчик: {customer[:100]}"
                if region:
                    user_prompt += f"\nРегион: {region}"

            # Генерируем название
            generated_name = self.llm.generate(system_prompt, user_prompt)
            generated_name = generated_name.strip().strip('"').strip("'")

            if not self._validate_no_hallucination(generated_name, original_name, tender_data):
                logger.warning(f"⚠️ Галлюцинация: '{generated_name}' не из '{original_name[:60]}'. Fallback.")
                return self._fallback_short_name(original_name, max_length)

            # Проверяем длину и обрезаем если нужно
            if len(generated_name) > max_length:
                generated_name = generated_name[:max_length-3] + "..."

            # Сохраняем в кэш
            self._save_to_memory_cache(cache_key, generated_name)

            logger.info(f"✅ Сгенерировано название: {generated_name[:50]}...")
            return generated_name

        except Exception as e:
            logger.error(f"❌ Ошибка генерации названия: {e}")
            return self._fallback_short_name(original_name, max_length)

    _STOP_WORDS = {
        'и', 'в', 'на', 'по', 'для', 'с', 'от', 'из', 'к', 'до', 'о', 'об',
        'не', 'а', 'но', 'или', 'за', 'при', 'без', 'под', 'над', 'между',
        'через', 'после', 'перед', 'около', 'у', 'со', 'во', 'ко',
        'нужд', 'целях', 'рамках', 'также', 'иных', 'иного', 'т.п.',
    }

    def _validate_no_hallucination(
        self,
        generated: str,
        original_name: str,
        tender_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        source_text = original_name.lower()
        if tender_data:
            summary = tender_data.get('summary', '') or ''
            description = tender_data.get('description', '') or ''
            source_text += ' ' + summary.lower() + ' ' + description.lower()

        source_text = re.sub(r'<[^>]+>', ' ', source_text)
        source_words = set(re.findall(r'[а-яёa-z]{3,}', source_text))

        gen_words = set(re.findall(r'[а-яёa-z]{3,}', generated.lower()))
        gen_words -= self._STOP_WORDS

        if not gen_words:
            return True

        def _same_root(a: str, b: str) -> bool:
            if a in b or b in a:
                return True
            prefix = min(len(a), len(b), 5)
            return prefix >= 4 and a[:prefix] == b[:prefix]

        matched = sum(1 for w in gen_words if any(_same_root(w, sw) for sw in source_words))
        ratio = matched / len(gen_words)
        return ratio >= 0.5

    def _fallback_short_name(self, original_name: str, max_length: int) -> str:
        """
        Fallback метод для создания короткого названия без LLM.
        Обрабатывает типовые юридические конструкции и извлекает суть.
        """
        import re

        # Сначала вырезаем мусорный префикс (аукцион/конкурс + номер)
        name = self._strip_procedure_prefix(original_name)

        # Если strip ничего не дал и это чисто мусорное название — возвращаем тип процедуры
        if name == original_name and self._is_only_procedure_number(original_name):
            for prefix in self._PROCEDURE_PREFIXES:
                if original_name.lower().startswith(prefix):
                    return prefix[0].upper() + prefix[1:]

        if len(name) <= max_length:
            return name

        # === ОБРАБОТКА ТИПОВЫХ ПАТТЕРНОВ ===

        # Паттерн: "Закупка, осуществляемая в соответствии с частью X статьи 93..."
        # Пытаемся найти суть после юридической формулировки
        article_patterns = [
            r'в соответствии с.*?(?:статьи?\s*\d+|закона).*?(?:[-–—]\s*|\.\s+|\s*,\s*)(.+)',
            r'статьи?\s*93.*?(?:закона|ФЗ).*?(?:[-–—]\s*|\.\s+|\s*,\s*)(.+)',
            r'единственного поставщика.*?(?:[-–—]\s*|\.\s+|\s*,\s*)(.+)',
        ]

        for pattern in article_patterns:
            match = re.search(pattern, name, re.IGNORECASE)
            if match and match.group(1):
                extracted = match.group(1).strip()
                if len(extracted) >= 10:  # Минимальная осмысленная длина
                    name = extracted
                    break

        # Удаляем распространённые бессодержательные начала
        useless_starts = [
            r'^закупка,?\s*осуществляемая\s+',
            r'^поставка\s+товара\s*,?\s*',
            r'^выполнение\s+работ\s+по\s+',
            r'^оказание\s+услуг\s+по\s+',
            r'^приобретение\s+',
            r'^на\s+поставку\s+',
            r'^для\s+нужд\s+',
        ]

        for pattern in useless_starts:
            name = re.sub(pattern, '', name, flags=re.IGNORECASE)

        # Удаляем юридические ссылки в конце
        name = re.sub(r'\s*\(?\s*в соответствии.*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\s*\(?\s*согласно.*$', '', name, flags=re.IGNORECASE)

        # Если после очистки осталось мало текста, используем оригинал
        if len(name.strip()) < 15:
            name = original_name

        name = name.strip()

        # Финальная обрезка по словам
        if len(name) <= max_length:
            return name

        words = name[:max_length].split()
        if len(' '.join(words)) + 3 <= max_length:
            return ' '.join(words) + '...'
        else:
            return ' '.join(words[:-1]) + '...' if len(words) > 1 else name[:max_length-3] + '...'

    def clear_cache(self):
        """Очищает in-memory кэш."""
        self._memory_cache.clear()
        logger.info("🗑️ Кэш названий очищен")


# Singleton экземпляр для использования в приложении
_generator_instance: Optional[TenderNameGenerator] = None


def get_name_generator() -> TenderNameGenerator:
    """
    Получает singleton экземпляр генератора названий.

    Returns:
        Экземпляр TenderNameGenerator
    """
    global _generator_instance

    if _generator_instance is None:
        _generator_instance = TenderNameGenerator()

    return _generator_instance


def generate_tender_name(
    original_name: str,
    tender_data: Optional[Dict[str, Any]] = None,
    max_length: int = 80
) -> str:
    """
    Удобная функция для генерации короткого названия тендера.

    Args:
        original_name: Оригинальное название
        tender_data: Дополнительные данные тендера
        max_length: Максимальная длина

    Returns:
        Короткое AI-сгенерированное название

    Example:
        >>> name = generate_tender_name(
        ...     "Поставка медицинского оборудования для нужд ГБУЗ Городская больница №1",
        ...     max_length=80
        ... )
        >>> print(name)
        'Поставка медицинского оборудования'
    """
    generator = get_name_generator()
    return generator.generate_short_name(original_name, tender_data, max_length)


# ============================================
# ПРИМЕР ИСПОЛЬЗОВАНИЯ
# ============================================

if __name__ == '__main__':
    import asyncio
    from dotenv import load_dotenv

    load_dotenv()

    # Тестовые примеры
    test_tenders = [
        "Поставка компьютерного оборудования для нужд ГБОУ СОШ №123 в рамках реализации программы цифровизации образования",
        "Выполнение работ по капитальному ремонту фасада здания административного корпуса",
        "Оказание услуг по организации питания обучающихся",
        "Короткое название"  # Этот не должен генерироваться
    ]

    generator = TenderNameGenerator()

    print("=== Тестирование AI Name Generator ===\n")

    for i, original in enumerate(test_tenders, 1):
        print(f"{i}. Оригинал ({len(original)} символов):")
        print(f"   {original}\n")

        short = generator.generate_short_name(original, max_length=80)

        print(f"   Короткое ({len(short)} символов):")
        print(f"   {short}\n")
        print("-" * 80 + "\n")

    # Проверка кэширования
    print("=== Тест кэширования ===")
    print("Повторная генерация для первого тендера (должна взять из кэша):")
    short_cached = generator.generate_short_name(test_tenders[0])
    print(f"{short_cached}\n")
