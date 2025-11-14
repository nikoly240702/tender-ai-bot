"""
Универсальный адаптер для работы с различными LLM API.
Поддерживает: Anthropic Claude, OpenAI, Groq, Google Gemini, Ollama.
"""

import os
import json
import time
from typing import Dict, Any, Optional, List
from abc import ABC, abstractmethod


class LLMAdapter(ABC):
    """Базовый класс для всех LLM адаптеров."""

    def __init__(self, model: str, max_tokens: int = 4096, temperature: float = 0.3,
                 max_retries: int = 3, retry_delay: int = 2):
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Генерирует ответ от LLM."""
        pass

    def _retry_with_backoff(self, func, *args, **kwargs):
        """Выполняет функцию с повторными попытками."""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                break
        raise Exception(f"Не удалось выполнить запрос после {self.max_retries} попыток: {last_error}")


class AnthropicAdapter(LLMAdapter):
    """Адаптер для Anthropic Claude API."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        from anthropic import Anthropic
        self.client = Anthropic(api_key=api_key)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        def _call():
            message = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}]
            )
            return message.content[0].text

        return self._retry_with_backoff(_call)


class OpenAIAdapter(LLMAdapter):
    """Адаптер для OpenAI API (GPT-4, GPT-3.5-turbo)."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content

        return self._retry_with_backoff(_call)


class GroqAdapter(LLMAdapter):
    """Адаптер для Groq API (БЕСПЛАТНЫЙ!)."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        from groq import Groq
        self.client = Groq(api_key=api_key)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return response.choices[0].message.content

        return self._retry_with_backoff(_call)


class GeminiAdapter(LLMAdapter):
    """Адаптер для Google Gemini API."""

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self.client = genai.GenerativeModel(self.model)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        def _call():
            full_prompt = f"{system_prompt}\n\n{user_prompt}"
            response = self.client.generate_content(
                full_prompt,
                generation_config={
                    'temperature': self.temperature,
                    'max_output_tokens': self.max_tokens,
                }
            )
            return response.text

        return self._retry_with_backoff(_call)


class OllamaAdapter(LLMAdapter):
    """Адаптер для Ollama (локальные модели)."""

    def __init__(self, base_url: str = "http://localhost:11434", **kwargs):
        super().__init__(**kwargs)
        import requests
        self.base_url = base_url
        self.session = requests.Session()

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        def _call():
            response = self.session.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model,
                    "prompt": f"{system_prompt}\n\n{user_prompt}",
                    "stream": False,
                    "options": {
                        "temperature": self.temperature,
                        "num_predict": self.max_tokens
                    }
                }
            )
            response.raise_for_status()
            return response.json()["response"]

        return self._retry_with_backoff(_call)


class LLMFactory:
    """Фабрика для создания LLM адаптеров."""

    ADAPTERS = {
        'anthropic': AnthropicAdapter,
        'openai': OpenAIAdapter,
        'groq': GroqAdapter,
        'gemini': GeminiAdapter,
        'ollama': OllamaAdapter,
    }

    # Рекомендуемые модели для каждого провайдера
    RECOMMENDED_MODELS = {
        'anthropic': 'claude-sonnet-4-20250514',
        'openai': 'gpt-4o-mini',  # Дешевая и качественная
        'groq': 'llama-3.1-70b-versatile',  # БЕСПЛАТНО!
        'gemini': 'gemini-1.5-flash',  # Бесплатный tier
        'ollama': 'llama3.1:8b',  # Локально
    }

    @classmethod
    def create(cls, provider: str, api_key: Optional[str] = None,
               model: Optional[str] = None, **kwargs) -> LLMAdapter:
        """
        Создает LLM адаптер для указанного провайдера.

        Args:
            provider: Название провайдера ('anthropic', 'openai', 'groq', 'gemini', 'ollama')
            api_key: API ключ (не требуется для ollama)
            model: Название модели (если None, используется рекомендуемая)
            **kwargs: Дополнительные параметры

        Returns:
            Экземпляр LLM адаптера

        Example:
            >>> adapter = LLMFactory.create('groq', api_key='gsk_...')
            >>> response = adapter.generate("You are an expert", "Analyze this...")
        """
        provider = provider.lower()

        if provider not in cls.ADAPTERS:
            raise ValueError(
                f"Неизвестный провайдер: {provider}. "
                f"Доступные: {', '.join(cls.ADAPTERS.keys())}"
            )

        # Используем рекомендуемую модель, если не указана
        if model is None:
            model = cls.RECOMMENDED_MODELS.get(provider)

        adapter_class = cls.ADAPTERS[provider]

        # Фильтруем kwargs для каждого провайдера
        filtered_kwargs = {}
        common_params = ['max_tokens', 'temperature', 'max_retries', 'retry_delay']
        for param in common_params:
            if param in kwargs:
                filtered_kwargs[param] = kwargs[param]

        # Создаем адаптер
        if provider == 'ollama':
            if 'ollama_base_url' in kwargs:
                filtered_kwargs['base_url'] = kwargs['ollama_base_url']
            return adapter_class(model=model, **filtered_kwargs)
        else:
            if not api_key:
                raise ValueError(f"API ключ обязателен для провайдера {provider}")
            return adapter_class(api_key=api_key, model=model, **filtered_kwargs)

    @classmethod
    def list_providers(cls) -> List[Dict[str, Any]]:
        """Возвращает список доступных провайдеров с описаниями."""
        return [
            {
                'name': 'anthropic',
                'description': 'Anthropic Claude - высокое качество',
                'cost': 'Платный',
                'recommended_model': cls.RECOMMENDED_MODELS['anthropic']
            },
            {
                'name': 'openai',
                'description': 'OpenAI GPT - хорошее качество, доступная цена',
                'cost': 'Платный (дешевый)',
                'recommended_model': cls.RECOMMENDED_MODELS['openai']
            },
            {
                'name': 'groq',
                'description': 'Groq - очень быстрый, БЕСПЛАТНЫЙ!',
                'cost': 'Бесплатный',
                'recommended_model': cls.RECOMMENDED_MODELS['groq']
            },
            {
                'name': 'gemini',
                'description': 'Google Gemini - бесплатный tier',
                'cost': 'Бесплатный/Платный',
                'recommended_model': cls.RECOMMENDED_MODELS['gemini']
            },
            {
                'name': 'ollama',
                'description': 'Ollama - локальные модели на вашем ПК',
                'cost': 'Бесплатный (локально)',
                'recommended_model': cls.RECOMMENDED_MODELS['ollama']
            },
        ]


if __name__ == "__main__":
    # Пример использования
    print("=== Доступные LLM провайдеры ===\n")
    for provider in LLMFactory.list_providers():
        print(f"{provider['name'].upper()}")
        print(f"  Описание: {provider['description']}")
        print(f"  Стоимость: {provider['cost']}")
        print(f"  Рекомендуемая модель: {provider['recommended_model']}\n")

    # Пример создания адаптера
    # adapter = LLMFactory.create('groq', api_key='your-key')
    # response = adapter.generate("You are a helpful assistant", "Hello!")
    # print(response)
