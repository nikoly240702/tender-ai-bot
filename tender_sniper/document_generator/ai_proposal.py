"""
AI-генерация текста технического предложения через GPT-4o-mini.

Паттерн из ai_relevance_checker.py — sync OpenAI client + run_in_executor.
"""

import os
import logging
import asyncio
import functools
from typing import Dict, Any, Optional

from openai import OpenAI

logger = logging.getLogger(__name__)


class AIProposalGenerator:
    """Генератор текста технического предложения через GPT-4o-mini."""

    MODEL = "gpt-4o-mini"
    MAX_TOKENS = 1500

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            self.client = None
            logger.warning("OpenAI API key not found. AI proposal generation disabled.")

    async def generate_proposal_text(
        self,
        tender_name: str,
        tender_requirements: str = "",
        company_profile: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Генерация текста технического предложения.

        Args:
            tender_name: Название тендера
            tender_requirements: Требования из документации (если есть)
            company_profile: Профиль компании

        Returns:
            Текст предложения или None при ошибке
        """
        if not self.client:
            return None

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                functools.partial(self._generate_sync, tender_name, tender_requirements, company_profile)
            )
            return result
        except Exception as e:
            logger.error(f"AI proposal generation error: {e}", exc_info=True)
            return None

    def _generate_sync(
        self,
        tender_name: str,
        tender_requirements: str,
        company_profile: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        """Синхронная генерация через OpenAI API."""
        company_info = ""
        if company_profile:
            company_info = f"""
Информация о компании:
- Название: {company_profile.get('company_name', '')}
- Опыт: {company_profile.get('experience_description', 'не указан')}
- Лицензии: {company_profile.get('licenses_text', 'не указаны')}
- СМП: {'Да' if company_profile.get('smp_status') else 'Нет'}
"""

        requirements_section = ""
        if tender_requirements:
            requirements_section = f"\nТребования из документации:\n{tender_requirements[:1000]}"

        prompt = f"""Напиши профессиональный текст технического предложения для участия в тендере.

Название тендера: {tender_name}
{requirements_section}
{company_info}

Требования к тексту:
1. Официальный деловой стиль
2. 3-5 абзацев
3. Укажи готовность выполнить работы в полном объёме
4. Опиши подход к выполнению (общий, но конкретный)
5. Упомяни сроки и качество
6. Если есть информация о компании — используй её
7. Не упоминай конкретные цены
8. Текст на русском языке

Верни только текст предложения, без заголовков и нумерации."""

        try:
            response = self.client.chat.completions.create(
                model=self.MODEL,
                messages=[
                    {"role": "system", "content": "Ты эксперт по подготовке тендерной документации в России. "
                     "Пишешь профессиональные технические предложения для государственных закупок."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=self.MAX_TOKENS,
                temperature=0.7,
            )
            text = response.choices[0].message.content.strip()
            logger.info(f"AI proposal generated: {len(text)} chars for '{tender_name[:50]}'")
            return text
        except Exception as e:
            logger.error(f"OpenAI API error in proposal generation: {e}")
            return None
