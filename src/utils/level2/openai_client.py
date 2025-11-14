"""
OpenAI API клиент с продвинутыми возможностями:
- Retry логика
- Логирование
- Обработка ошибок
- Async поддержка
"""

import os
import json
import asyncio
from typing import Dict, Any, Optional, List
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)
from loguru import logger
import openai
from openai import OpenAI, AsyncOpenAI


class OpenAIClient:
    """
    Wrapper для OpenAI API с продвинутыми возможностями
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4-turbo-preview",
        max_tokens: int = 4000,
        temperature: float = 0.1,
        max_retries: int = 3
    ):
        """
        Args:
            api_key: OpenAI API ключ (если None, берется из env)
            model: Модель для использования
            max_tokens: Максимум токенов в ответе
            temperature: Температура (0.0-2.0)
            max_retries: Максимум попыток при ошибке
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not found. Set OPENAI_API_KEY env variable.")
        
        self.model = model
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.max_retries = max_retries
        
        # Инициализация клиентов
        self.client = OpenAI(api_key=self.api_key)
        self.async_client = AsyncOpenAI(api_key=self.api_key)
        
        logger.info(f"OpenAI client initialized with model: {model}")
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((openai.RateLimitError, openai.APITimeoutError))
    )
    def query(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> str:
        """
        Синхронный запрос к OpenAI API
        
        Args:
            prompt: Пользовательский промпт
            system_prompt: Системный промпт (опционально)
            response_format: Формат ответа, например {"type": "json_object"}
            **kwargs: Дополнительные параметры для API
        
        Returns:
            Текст ответа от модели
        """
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": prompt})
        
        # Параметры запроса
        request_params = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        # Добавляем response_format если указан
        if response_format:
            request_params["response_format"] = response_format
        
        try:
            logger.debug(f"Sending request to OpenAI API (model: {request_params['model']})")
            
            response = self.client.chat.completions.create(**request_params)
            
            result = response.choices[0].message.content
            
            logger.debug(f"Received response ({len(result)} characters)")
            
            return result
            
        except openai.RateLimitError as e:
            logger.error(f"Rate limit exceeded: {e}")
            raise
        except openai.APIError as e:
            logger.error(f"OpenAI API error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((openai.RateLimitError, openai.APITimeoutError))
    )
    async def query_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_format: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> str:
        """
        Асинхронный запрос к OpenAI API
        
        Args:
            prompt: Пользовательский промпт
            system_prompt: Системный промпт (опционально)
            response_format: Формат ответа
            **kwargs: Дополнительные параметры
        
        Returns:
            Текст ответа от модели
        """
        messages = []
        
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        
        messages.append({"role": "user", "content": prompt})
        
        request_params = {
            "model": kwargs.get("model", self.model),
            "messages": messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        
        if response_format:
            request_params["response_format"] = response_format
        
        try:
            logger.debug(f"Sending async request to OpenAI API")
            
            response = await self.async_client.chat.completions.create(**request_params)
            
            result = response.choices[0].message.content
            
            logger.debug(f"Received async response ({len(result)} characters)")
            
            return result
            
        except Exception as e:
            logger.error(f"Async query error: {e}")
            raise
    
    def query_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Запрос с ожиданием JSON ответа
        
        Args:
            prompt: Промпт
            system_prompt: Системный промпт
            **kwargs: Дополнительные параметры
        
        Returns:
            Распарсенный JSON объект
        """
        # Добавляем инструкцию возвращать JSON
        enhanced_prompt = f"""{prompt}

ВАЖНО: Верни ТОЛЬКО валидный JSON без дополнительного текста.
Не используй markdown форматирование (```json).
Ответ должен начинаться с {{ и заканчиваться }}."""

        # Используем JSON mode если модель поддерживает
        response_format = None
        if self.model in ["gpt-4-turbo-preview", "gpt-4-1106-preview", "gpt-3.5-turbo-1106"]:
            response_format = {"type": "json_object"}
        
        response_text = self.query(
            prompt=enhanced_prompt,
            system_prompt=system_prompt,
            response_format=response_format,
            **kwargs
        )
        
        # Парсим JSON
        try:
            # Убираем возможные markdown метки
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            
            result = json.loads(cleaned)
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {e}")
            logger.error(f"Response was: {response_text[:500]}")
            raise ValueError(f"Invalid JSON response: {e}")
    
    async def query_json_async(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Асинхронный запрос с ожиданием JSON ответа
        """
        enhanced_prompt = f"""{prompt}

ВАЖНО: Верни ТОЛЬКО валидный JSON без дополнительного текста."""

        response_format = None
        if self.model in ["gpt-4-turbo-preview", "gpt-4-1106-preview", "gpt-3.5-turbo-1106"]:
            response_format = {"type": "json_object"}
        
        response_text = await self.query_async(
            prompt=enhanced_prompt,
            system_prompt=system_prompt,
            response_format=response_format,
            **kwargs
        )
        
        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()
            
            return json.loads(cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse async JSON response: {e}")
            raise ValueError(f"Invalid JSON response: {e}")
    
    async def query_multiple_async(
        self,
        prompts: List[str],
        system_prompt: Optional[str] = None,
        **kwargs
    ) -> List[str]:
        """
        Параллельные асинхронные запросы (для self-consistency)
        
        Args:
            prompts: Список промптов
            system_prompt: Системный промпт
            **kwargs: Дополнительные параметры
        
        Returns:
            Список ответов в том же порядке
        """
        tasks = [
            self.query_async(prompt, system_prompt, **kwargs)
            for prompt in prompts
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обработка ошибок
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"Query {i} failed: {result}")
                processed_results.append(None)
            else:
                processed_results.append(result)
        
        return processed_results


def create_client_from_env() -> OpenAIClient:
    """
    Создать клиент из переменных окружения
    """
    return OpenAIClient(
        api_key=os.getenv("OPENAI_API_KEY"),
        model=os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview"),
        max_tokens=int(os.getenv("OPENAI_MAX_TOKENS", "4000")),
        temperature=float(os.getenv("OPENAI_TEMPERATURE", "0.1")),
        max_retries=int(os.getenv("MAX_RETRIES", "3"))
    )
