"""
AI Name Generator для тендеров.

Генерирует короткие, понятные названия для тендеров вместо длинных юридических текстов.
Использует LLM и кэширует результаты для экономии API запросов.
"""

import os
import sys
import logging
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent.parent))

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
                max_tokens=100,  # Короткие названия - мало токенов
                temperature=0.3  # Низкая температура для стабильности
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

        # Если оригинальное название уже короткое, возвращаем его
        if len(original_name) <= max_length:
            return original_name

        # Проверяем кэш
        cache_key = self._get_cache_key(original_name)
        cached_name = self._get_from_memory_cache(cache_key)
        if cached_name:
            logger.debug(f"💾 Название найдено в кэше")
            return cached_name

        # Если LLM недоступен, возвращаем обрезанное оригинальное название
        if not self.llm:
            logger.debug("⚠️ LLM недоступен, используем обрезанное название")
            return self._fallback_short_name(original_name, max_length)

        # Генерируем через LLM
        try:
            logger.info(f"🤖 Генерация короткого названия через {self.provider}...")

            system_prompt = """Ты - эксперт по государственным закупкам.
Твоя задача - создавать короткие, понятные названия для тендеров.

ВАЖНО:
- Название должно быть максимум 80 символов
- Убирай юридические формулировки и бюрократию
- Оставляй только суть: ЧТО покупают
- Используй простой русский язык
- Не используй кавычки в начале и конце"""

            user_prompt = f"""Исходное название тендера:
{original_name}

Создай короткое понятное название (максимум 80 символов), которое отражает суть закупки.
Отвечай ТОЛЬКО названием, без пояснений и кавычек."""

            # Добавляем контекст из tender_data если есть
            if tender_data:
                customer = tender_data.get('customer_name')
                region = tender_data.get('region')
                if customer:
                    user_prompt += f"\n\nЗаказчик: {customer[:100]}"
                if region:
                    user_prompt += f"\nРегион: {region}"

            # Генерируем название
            generated_name = self.llm.generate(system_prompt, user_prompt)
            generated_name = generated_name.strip().strip('"').strip("'")

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

    def _fallback_short_name(self, original_name: str, max_length: int) -> str:
        """
        Fallback метод для создания короткого названия без LLM.
        Просто умно обрезает оригинальное название.
        """
        if len(original_name) <= max_length:
            return original_name

        # Обрезаем по словам
        words = original_name[:max_length].split()

        # Убираем последнее слово если оно обрезано
        if len(' '.join(words)) + 3 <= max_length:
            return ' '.join(words) + '...'
        else:
            return ' '.join(words[:-1]) + '...'

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
