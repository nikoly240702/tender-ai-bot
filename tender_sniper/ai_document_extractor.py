"""
AI Document Extractor - извлечение структурированных данных из тендерной документации.

Использует GPT-4o-mini для извлечения ключевой информации из PDF/DOCX файлов.
PREMIUM функция - доступна только для Premium пользователей.

Архитектура: flat schema + multi-pass extraction + chunking + validation + red flags.
"""

import asyncio
import json
import logging
import os
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta

from tender_sniper.ai_features import AIFeatureGate, format_ai_feature_locked_message

logger = logging.getLogger(__name__)

# Месяцы для нормализации дат
_MONTHS_RU = {
    'января': '01', 'февраля': '02', 'марта': '03', 'апреля': '04',
    'мая': '05', 'июня': '06', 'июля': '07', 'августа': '08',
    'сентября': '09', 'октября': '10', 'ноября': '11', 'декабря': '12',
    'январь': '01', 'февраль': '02', 'март': '03', 'апрель': '04',
    'май': '05', 'июнь': '06', 'июль': '07', 'август': '08',
    'сентябрь': '09', 'октябрь': '10', 'ноябрь': '11', 'декабрь': '12',
}


class TenderDocumentExtractor:
    """
    Извлекает структурированные данные из тендерной документации.

    Multi-pass архитектура:
    - Pass 1: Сроки и логистика (submission_deadline, execution_deadline, delivery_address)
    - Pass 2: Финансовые условия (advance, payment, security, guarantee)
    - Pass 3: Позиции и требования (items, licenses, experience, summary)
    """

    MODEL = "gpt-4o-mini"
    CHUNK_MAX_CHARS = 25000
    CHUNK_OVERLAP = 2000
    MAX_CHUNKS = 3  # Limit chunks to avoid rate limits
    RETRY_ATTEMPTS = 3
    RETRY_BASE_DELAY = 2.0  # seconds

    # --- Pass 1: Сроки и логистика ---
    PROMPT_DATES = """Ты эксперт по анализу тендерной документации госзакупок России.

Извлеки ТОЛЬКО сроки и адрес поставки. Ответ СТРОГО в JSON:

{
    "submission_deadline": "дата и время окончания подачи заявок, формат ДД.ММ.ГГГГ ЧЧ:ММ МСК. Если нет — 'Не указано'",
    "execution_deadline": "срок исполнения/поставки ДОСЛОВНО как в документе. Например: '10 рабочих дней с момента заключения контракта'. Если нет — 'Не указано'",
    "delivery_address": "адрес ПОСТАВКИ (НЕ юридический адрес заказчика!). Если нет — 'Не указано'"
}

ПРАВИЛА:
1. submission_deadline — ищи "окончание подачи заявок", "дата окончания срока подачи", "заявки принимаются до"
2. execution_deadline — ищи "срок исполнения", "срок поставки", "срок выполнения работ", пиши ДОСЛОВНО
3. delivery_address — ищи "место поставки", "адрес доставки", "место выполнения работ"
4. Если информации нет — пиши "Не указано"

"""

    # --- Pass 2: Финансовые условия ---
    PROMPT_FINANCE = """Ты эксперт по анализу тендерной документации госзакупок России.

Извлеки ТОЛЬКО финансовые условия. Ответ СТРОГО в JSON:

{
    "advance_percent": "размер аванса, например '30%'. Если аванс не предусмотрен — 'Не предусмотрен'. Если не указано — 'Не указано'",
    "payment_deadline": "срок оплаты, например '15 рабочих дней после подписания акта'. Если нет — 'Не указано'",
    "application_security": "обеспечение заявки, например '1% от НМЦК' или '50 000 руб.' или 'Не требуется'. Если нет — 'Не указано'",
    "contract_security": "обеспечение исполнения контракта, например '5% от НМЦК' или '100 000 руб.'. Если нет — 'Не указано'",
    "bank_guarantee_allowed": "допускается ли банковская гарантия: 'Да', 'Нет' или 'Не указано'"
}

ПРАВИЛА:
1. Ищи "обеспечение заявки", "обеспечение исполнения контракта", "банковская гарантия"
2. Ищи "аванс", "авансовый платёж", "предоплата"
3. Ищи "оплата", "расчёт", "срок оплаты"
4. Указывай проценты с символом %, суммы с "руб."

"""

    # --- Pass 3: Позиции и требования ---
    PROMPT_ITEMS = """Ты эксперт по анализу тендерной документации госзакупок России.

Извлеки позиции закупки и требования к участнику. Ответ СТРОГО в JSON:

{
    "items_count": "число позиций/наименований в спецификации, например '3'. ОБЯЗАТЕЛЬНО!",
    "items_description": "нумерованный список позиций В ОДНУ СТРОКУ. Формат: '1. Название (кол-во) — ключевые хар-ки; 2. Название (кол-во) — хар-ки'. Максимум 10 позиций. ОБЯЗАТЕЛЬНО!",
    "licenses_required": "конкретные лицензии: 'Лицензия ФСБ', 'Лицензия ФСТЭК', 'СРО' и т.п. Если не требуются — 'Не требуются'",
    "experience_required": "требования к опыту, например 'Не менее 3 лет в сфере IT'. Если нет — 'Не указано'",
    "summary": "1-2 предложения: что закупают, количество, ключевые условия"
}

ПРАВИЛА:
1. items_count — ВСЕГДА заполни! Посчитай позиции в спецификации/ТЗ
2. items_description — извлеки ВСЕ позиции (макс 10), для каждой: название, количество, ключевые хар-ки. Формат НУМЕРОВАННОГО СПИСКА В ОДНУ СТРОКУ через "; "
3. Если указан бренд/марка/модель — ОБЯЗАТЕЛЬНО включи в описание позиции
4. licenses_required — ТОЛЬКО конкретные лицензии (ФСБ, ФСТЭК, МЧС, СРО), НЕ общие фразы
5. summary — кратко, 1-2 предложения

"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('OPENAI_API_KEY')
        self._client = None

    @property
    def client(self):
        """Ленивая инициализация OpenAI клиента."""
        if self._client is None:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=self.api_key)
            except ImportError:
                logger.warning("OpenAI библиотека не установлена")
                return None
        return self._client

    def _build_context(self, tender_info: Optional[Dict[str, Any]]) -> str:
        """Формирует строку контекста из tender_info."""
        if not tender_info:
            return ""
        parts = []
        if tender_info.get('number'):
            parts.append(f"Номер закупки: {tender_info['number']}")
        if tender_info.get('price'):
            parts.append(f"НМЦ: {tender_info['price']:,.0f} руб.")
        if tender_info.get('customer'):
            parts.append(f"Заказчик: {tender_info['customer']}")
        if parts:
            return "ИНФОРМАЦИЯ О ТЕНДЕРЕ:\n" + "\n".join(parts) + "\n\n"
        return ""

    def _chunk_text(self, text: str) -> List[str]:
        """Разбивает текст на chunks с overlap по границам предложений.
        Ограничивает максимум MAX_CHUNKS чанков для контроля rate limits."""
        max_chars = self.CHUNK_MAX_CHARS
        overlap = self.CHUNK_OVERLAP

        if len(text) <= max_chars:
            return [text]

        # Для очень длинных документов — умная обрезка перед chunking
        max_total = max_chars * self.MAX_CHUNKS
        if len(text) > max_total:
            # Берём начало (основные условия) + конец (приложения со спецификациями)
            head_size = max_total * 2 // 3
            tail_size = max_total - head_size
            text = text[:head_size] + "\n\n[...]\n\n" + text[-tail_size:]
            logger.info(f"Документ обрезан: {len(text)} символов (head={head_size}, tail={tail_size})")

        chunks = []
        start = 0
        while start < len(text):
            end = start + max_chars

            if end >= len(text):
                chunks.append(text[start:])
                break

            # Ищем конец предложения ближе к границе
            search_zone = text[end - 500:end]
            last_dot = search_zone.rfind('.')
            if last_dot != -1:
                end = end - 500 + last_dot + 1
            else:
                # Ищем перенос строки
                last_nl = search_zone.rfind('\n')
                if last_nl != -1:
                    end = end - 500 + last_nl + 1

            chunks.append(text[start:end])
            start = end - overlap

            if len(chunks) >= self.MAX_CHUNKS:
                break

        logger.info(f"Текст разбит на {len(chunks)} chunk(s), overlap={overlap}")
        return chunks

    async def _extract_pass(
        self,
        text: str,
        prompt: str,
        context: str,
        max_tokens: int = 500
    ) -> Dict[str, Any]:
        """Один pass извлечения через API с retry на 429."""
        for attempt in range(self.RETRY_ATTEMPTS):
            try:
                response = await self.client.chat.completions.create(
                    model=self.MODEL,
                    messages=[
                        {"role": "user", "content": prompt + context + "ДОКУМЕНТАЦИЯ ТЕНДЕРА:\n" + text}
                    ],
                    max_tokens=max_tokens,
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
                result_text = response.choices[0].message.content.strip()
                return json.loads(result_text)
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse error in pass: {e}")
                return {}
            except Exception as e:
                error_str = str(e)
                if '429' in error_str and attempt < self.RETRY_ATTEMPTS - 1:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.warning(f"Rate limit hit, retry {attempt + 1}/{self.RETRY_ATTEMPTS} in {delay}s")
                    await asyncio.sleep(delay)
                    continue
                logger.error(f"API error in pass (attempt {attempt + 1}): {e}")
                return {}
        return {}

    def _merge_chunk_results(self, all_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Объединяет результаты из нескольких chunks."""
        if not all_results:
            return {}
        if len(all_results) == 1:
            return all_results[0]

        # Поля, где берём первое непустое значение
        single_fields = [
            'submission_deadline', 'execution_deadline', 'delivery_address',
            'advance_percent', 'payment_deadline', 'application_security',
            'contract_security', 'bank_guarantee_allowed',
            'licenses_required', 'experience_required', 'summary',
        ]

        final = {}
        for field in single_fields:
            for result in all_results:
                val = result.get(field)
                if val and str(val).strip() and str(val).strip() != 'Не указано':
                    final[field] = val
                    break
            if field not in final:
                # Берём хотя бы "Не указано" если есть
                for result in all_results:
                    if field in result:
                        final[field] = result[field]
                        break

        # items_description — объединяем из всех chunks
        items_parts = []
        for result in all_results:
            desc = result.get('items_description', '')
            if desc and str(desc).strip() and str(desc) != 'Не указано':
                items_parts.append(str(desc))
        if items_parts:
            final['items_description'] = '; '.join(items_parts)
        elif 'items_description' not in final:
            final['items_description'] = 'Не указано'

        # items_count — максимальное значение
        max_count = 0
        for result in all_results:
            try:
                count = int(result.get('items_count', 0))
                max_count = max(max_count, count)
            except (ValueError, TypeError):
                pass
        final['items_count'] = str(max_count) if max_count > 0 else 'Не указано'

        return final

    def _validate_and_normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Нормализует и валидирует извлечённые данные."""
        # Все ожидаемые поля
        expected_fields = [
            'submission_deadline', 'execution_deadline', 'delivery_address',
            'items_count', 'items_description',
            'licenses_required', 'experience_required',
            'advance_percent', 'payment_deadline',
            'application_security', 'contract_security', 'bank_guarantee_allowed',
            'summary',
        ]

        # Заполняем пустые поля
        for field in expected_fields:
            if field not in data or not str(data[field]).strip():
                data[field] = 'Не удалось определить'

        # Нормализация дат: "20 февраля 2026" → "20.02.2026"
        for date_field in ['submission_deadline']:
            val = str(data.get(date_field, ''))
            normalized = _normalize_date_text(val)
            if normalized != val:
                data[date_field] = normalized

        # Нормализация items_count — должно быть число
        try:
            count = int(data.get('items_count', 0))
            data['items_count'] = str(count)
        except (ValueError, TypeError):
            # Оставляем как есть если не парсится
            pass

        # Нормализация bank_guarantee_allowed
        bg = str(data.get('bank_guarantee_allowed', '')).lower()
        if bg in ('да', 'true', 'допускается', 'разрешена'):
            data['bank_guarantee_allowed'] = 'Да'
        elif bg in ('нет', 'false', 'не допускается'):
            data['bank_guarantee_allowed'] = 'Нет'

        return data

    def _extract_red_flags(self, data: Dict[str, Any]) -> List[str]:
        """Определяет красные и жёлтые флаги из извлечённых данных."""
        flags = []

        # Лицензии ФСБ/ФСТЭК — красный флаг
        licenses = str(data.get('licenses_required', '')).lower()
        if 'фсб' in licenses:
            flags.append('Требуется лицензия ФСБ')
        if 'фстэк' in licenses:
            flags.append('Требуется лицензия ФСТЭК')
        if 'сро' in licenses:
            flags.append('Требуется членство в СРО')

        # Высокое обеспечение
        for field_name, label in [
            ('application_security', 'обеспечение заявки'),
            ('contract_security', 'обеспечение контракта'),
        ]:
            val = str(data.get(field_name, ''))
            pct_match = re.search(r'(\d+(?:[.,]\d+)?)\s*%', val)
            if pct_match:
                try:
                    pct = float(pct_match.group(1).replace(',', '.'))
                    if pct > 5:
                        flags.append(f'Высокое {label}: {val}')
                except (ValueError, TypeError):
                    pass

        # Короткий срок подачи
        submission = str(data.get('submission_deadline', ''))
        deadline_date = _parse_date(submission)
        if deadline_date:
            days_left = (deadline_date - datetime.now()).days
            if days_left < 0:
                flags.append('Срок подачи заявок истёк!')
            elif days_left < 3:
                flags.append(f'Срок подачи < 3 дней!')
            elif days_left < 7:
                flags.append(f'Срок подачи < 7 дней')

        return flags

    async def extract_from_text(
        self,
        document_text: str,
        subscription_tier: str = 'trial',
        tender_info: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], bool]:
        """
        Извлекает структурированные данные из текста документации.
        Multi-pass: 3 параллельных запроса на каждый chunk.

        Returns:
            Tuple[Dict, bool]: (извлечённые данные, is_ai_extracted)
        """
        # Проверяем Premium доступ
        gate = AIFeatureGate(subscription_tier)
        if not gate.can_use('summarization'):
            return ({
                'error': 'premium_required',
                'message': format_ai_feature_locked_message('summarization')
            }, False)

        if not self.api_key or not self.client:
            logger.warning("OpenAI API недоступен")
            return (self._create_fallback_extraction(document_text, tender_info), False)

        context = self._build_context(tender_info)
        chunks = self._chunk_text(document_text)

        try:
            all_results = []
            passes = [
                (self.PROMPT_DATES, 500),
                (self.PROMPT_FINANCE, 500),
                (self.PROMPT_ITEMS, 2000),
            ]
            for chunk_idx, chunk in enumerate(chunks):
                merged = {}
                # Run passes sequentially to avoid rate limit bursts
                for prompt, max_tok in passes:
                    result = await self._extract_pass(chunk, prompt, context, max_tokens=max_tok)
                    if isinstance(result, dict):
                        merged.update(result)
                all_results.append(merged)

            final = self._merge_chunk_results(all_results)
            final = self._validate_and_normalize(final)
            final['red_flags'] = self._extract_red_flags(final)
            final['_meta'] = {
                'extracted_at': datetime.now().isoformat(),
                'source': 'ai',
                'model': self.MODEL,
                'input_chars': len(document_text),
                'chunks': len(chunks),
                'passes': 3,
            }
            logger.info(
                f"AI-извлечение завершено: {len(document_text)} символов, "
                f"{len(chunks)} chunk(s), {len(final.get('red_flags', []))} red flags"
            )
            return (final, True)

        except Exception as e:
            logger.error(f"Ошибка AI-извлечения: {e}")
            return (self._create_fallback_extraction(document_text, tender_info), False)

    def _create_fallback_extraction(
        self,
        document_text: str,
        tender_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Создаёт базовое извлечение без AI (regex-based fallback) в flat формате."""
        text_lower = document_text.lower()

        result = {
            'submission_deadline': 'Не удалось определить',
            'execution_deadline': 'Не удалось определить',
            'delivery_address': 'Не удалось определить',
            'items_count': 'Не удалось определить',
            'items_description': 'Не удалось определить',
            'licenses_required': 'Не требуются',
            'experience_required': 'Не указано',
            'advance_percent': 'Не указано',
            'payment_deadline': 'Не указано',
            'application_security': 'Не указано',
            'contract_security': 'Не указано',
            'bank_guarantee_allowed': 'Да' if 'банковская гарантия' in text_lower else 'Не указано',
            'summary': 'Требуется детальный анализ документации.',
            'red_flags': [],
            '_meta': {
                'extracted_at': datetime.now().isoformat(),
                'source': 'fallback',
                'input_chars': len(document_text),
            }
        }

        # Обеспечение заявки
        m = re.search(r'обеспечение заявки[:\s]+(\d+(?:[.,]\d+)?)\s*%', text_lower)
        if m:
            result['application_security'] = f"{m.group(1).replace(',', '.')}% от НМЦК"

        # Обеспечение контракта
        m = re.search(r'обеспечение (?:исполнения )?контракта[:\s]+(\d+(?:[.,]\d+)?)\s*%', text_lower)
        if m:
            result['contract_security'] = f"{m.group(1).replace(',', '.')}% от НМЦК"

        # Сроки исполнения
        for pattern in [
            r'срок (?:исполнения|выполнения|поставки)[:\s]+(\d+)\s*(календарн\w+|рабочих)?\s*дн',
            r'в течение\s+(\d+)\s*(календарн\w+|рабочих)?\s*дн',
        ]:
            m = re.search(pattern, text_lower)
            if m:
                days = m.group(1)
                day_type = m.group(2) or 'календарных'
                result['execution_deadline'] = f"{days} {day_type} дней"
                break

        # Лицензии
        found_licenses = []
        license_patterns = [
            ('лицензия фсб', 'Лицензия ФСБ'),
            ('лицензия фстэк', 'Лицензия ФСТЭК'),
            ('лицензия мчс', 'Лицензия МЧС'),
            ('лицензия минздрав', 'Лицензия Минздрав'),
            ('медицинская лицензия', 'Медицинская лицензия'),
            ('строительная лицензия', 'Строительная лицензия'),
        ]
        for pattern, name in license_patterns:
            if pattern in text_lower:
                found_licenses.append(name)
        if 'сро' in text_lower or 'саморегулируемой' in text_lower:
            found_licenses.append('СРО')
        if found_licenses:
            result['licenses_required'] = ', '.join(found_licenses)

        # Опыт
        exp_match = re.search(
            r'опыт\w*\s+(?:работы\s+)?(?:не\s+)?менее\s+(\d+)\s*(?:лет|года)',
            text_lower
        )
        if exp_match:
            result['experience_required'] = f"Не менее {exp_match.group(1)} лет"

        # Red flags из fallback
        result['red_flags'] = self._extract_red_flags(result)

        return result

    async def extract_from_file(
        self,
        file_path: str,
        subscription_tier: str = 'trial',
        tender_info: Optional[Dict[str, Any]] = None
    ) -> Tuple[Dict[str, Any], bool]:
        """Извлекает данные напрямую из файла документации."""
        try:
            from src.document_processor.text_extractor import TextExtractor

            result = TextExtractor.extract_text(file_path)
            document_text = result['text']

            if not document_text or document_text.startswith('[Не удалось'):
                return ({
                    'error': 'extraction_failed',
                    'message': f"Не удалось извлечь текст из файла: {file_path}"
                }, False)

            return await self.extract_from_text(document_text, subscription_tier, tender_info)

        except Exception as e:
            logger.error(f"Ошибка извлечения из файла {file_path}: {e}")
            return ({
                'error': 'file_error',
                'message': str(e)
            }, False)


# --- Утилиты для нормализации дат ---

def _normalize_date_text(text: str) -> str:
    """Нормализует текстовую дату: '20 февраля 2026' → '20.02.2026'."""
    if not text or text in ('Не указано', 'Не удалось определить'):
        return text

    # Паттерн: "20 февраля 2026" или "20 февраля 2026 г."
    m = re.search(r'(\d{1,2})\s+([а-яё]+)\s+(\d{4})', text)
    if m:
        day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
        month_num = _MONTHS_RU.get(month_name)
        if month_num:
            formatted = f"{int(day):02d}.{month_num}.{year}"
            # Сохраняем время если есть
            time_match = re.search(r'(\d{1,2}:\d{2})', text)
            if time_match:
                formatted += f" {time_match.group(1)}"
            # Сохраняем МСК если есть
            if 'мск' in text.lower():
                formatted += " МСК"
            return formatted

    return text


def _parse_date(text: str) -> Optional[datetime]:
    """Парсит дату из строки для сравнения."""
    if not text:
        return None

    # ДД.ММ.ГГГГ
    m = re.match(r'(\d{2})\.(\d{2})\.(\d{4})', text)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


# --- Форматирование для Telegram ---

def format_extraction_for_telegram(extraction: Dict[str, Any], is_ai: bool) -> str:
    """
    Форматирует извлечённые данные для отображения в Telegram.
    Поддерживает как новый flat-формат, так и старый nested-формат.
    """
    if extraction.get('error'):
        return extraction.get('message', 'Ошибка извлечения данных')

    # Определяем формат: если есть execution_deadline (строка) — новый flat
    is_new_format = isinstance(extraction.get('execution_deadline'), str)

    if is_new_format:
        return _format_new_schema(extraction, is_ai)
    else:
        return _format_old_schema(extraction, is_ai)


def _classify_red_flag(flag: str) -> str:
    """Определяет иконку для red flag: ⛔ критичный или ⚠️ жёлтый."""
    flag_lower = flag.lower()
    critical_keywords = ['фсб', 'фстэк', 'истёк', 'истек', 'менее 3 дней', 'менее 2 дней', 'менее 1 дня', '1 день', '2 дня']
    for kw in critical_keywords:
        if kw in flag_lower:
            return '⛔'
    return '⚠️'


def _format_new_schema(extraction: Dict[str, Any], is_ai: bool) -> str:
    """Форматирование нового flat-формата."""
    lines = []

    if is_ai:
        lines.append("🔍 <b>AI-анализ документации</b>\n")
    else:
        lines.append("📋 <b>Базовый анализ документации</b>\n")

    # Подача заявок
    submission = extraction.get('submission_deadline', '')
    if submission and submission not in ('Не указано', 'Не удалось определить'):
        lines.append(f"⏰ <b>Подача заявок до:</b> {submission}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

    # Товары/работы
    items_desc = extraction.get('items_description', '')
    items_count = extraction.get('items_count', '')
    if items_desc and items_desc not in ('Не указано', 'Не удалось определить'):
        count_str = f" ({items_count} наим.)" if items_count and items_count not in ('Не указано', 'Не удалось определить') else ""
        lines.append(f"📦 <b>Товары/работы{count_str}:</b>")
        # Разбиваем нумерованный список по "; N." для читаемости
        items_lines = re.split(r';\s*(?=\d+\.)', items_desc)
        for item_line in items_lines[:10]:
            item_line = item_line.strip()
            if item_line:
                lines.append(f"• {item_line}")
        lines.append("")

    # Сроки
    exec_deadline = extraction.get('execution_deadline', '')
    delivery = extraction.get('delivery_address', '')
    has_deadlines = (exec_deadline and exec_deadline not in ('Не указано', 'Не удалось определить')) or \
                    (delivery and delivery not in ('Не указано', 'Не удалось определить'))
    if has_deadlines:
        lines.append("📅 <b>Сроки и логистика:</b>")
        if exec_deadline and exec_deadline not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Исполнение: {exec_deadline[:120]}")
        if delivery and delivery not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Адрес: {delivery[:120]}")
        lines.append("")

    # Требования
    licenses = extraction.get('licenses_required', '')
    experience = extraction.get('experience_required', '')
    has_reqs = (licenses and licenses not in ('Не указано', 'Не удалось определить', 'Не требуются')) or \
               (experience and experience not in ('Не указано', 'Не удалось определить'))
    if has_reqs:
        lines.append("⚠️ <b>Требования к участнику:</b>")
        if licenses and licenses not in ('Не указано', 'Не удалось определить', 'Не требуются'):
            lines.append(f"• Лицензии: {licenses}")
        if experience and experience not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Опыт: {experience}")
        lines.append("")

    # Обеспечение
    app_sec = extraction.get('application_security', '')
    con_sec = extraction.get('contract_security', '')
    bg = extraction.get('bank_guarantee_allowed', '')
    has_security = (app_sec and app_sec not in ('Не указано', 'Не удалось определить')) or \
                   (con_sec and con_sec not in ('Не указано', 'Не удалось определить'))
    if has_security:
        lines.append("💳 <b>Обеспечение:</b>")
        if app_sec and app_sec not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Заявка: {app_sec}")
        if con_sec and con_sec not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Контракт: {con_sec}")
        if bg and bg not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Банковская гарантия: {bg}")
        lines.append("")

    # Оплата
    advance = extraction.get('advance_percent', '')
    pay_deadline = extraction.get('payment_deadline', '')
    has_payment = (advance and advance not in ('Не указано', 'Не удалось определить')) or \
                  (pay_deadline and pay_deadline not in ('Не указано', 'Не удалось определить'))
    if has_payment:
        lines.append("💰 <b>Оплата:</b>")
        if advance and advance not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Аванс: {advance}")
        if pay_deadline and pay_deadline not in ('Не указано', 'Не удалось определить'):
            lines.append(f"• Срок оплаты: {pay_deadline}")
        lines.append("")

    # Red flags
    red_flags = extraction.get('red_flags', [])
    if red_flags:
        lines.append("🚩 <b>Риски:</b>")
        for flag in red_flags[:5]:
            icon = _classify_red_flag(flag)
            lines.append(f"• {icon} {flag}")
        lines.append("")

    # Резюме
    summary = extraction.get('summary', '')
    if summary and summary not in ('Не указано', 'Не удалось определить'):
        lines.append(f"📝 <b>Резюме:</b> {summary}")

    return "\n".join(lines)


def _format_old_schema(extraction: Dict[str, Any], is_ai: bool) -> str:
    """Форматирование старого nested-формата (обратная совместимость)."""
    lines = []

    if is_ai:
        lines.append("🔍 <b>AI-анализ документации</b>\n")
    else:
        lines.append("📋 <b>Базовый анализ документации</b>\n")

    # Площадка
    if extraction.get('trading_platform'):
        lines.append(f"• <b>Площадка:</b> {extraction['trading_platform']}")

    # Подача заявок (top-level)
    submission = extraction.get('submission_deadline')
    if submission:
        lines.append(f"⏰ <b>Подача заявок до:</b> {submission}")

    if extraction.get('trading_platform') or submission:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("")

    # Товарные позиции
    items = extraction.get('items', [])
    items_count = extraction.get('items_count')
    if not items:
        specs = extraction.get('technical_specs', {})
        items = specs.get('items_details', [])
        if not items_count:
            items_count = specs.get('items_count')

    if items:
        count_str = f" ({items_count} наим.)" if items_count else ""
        lines.append(f"📦 <b>Товары/работы{count_str}:</b>")
        for item in items[:10]:
            name = item.get('name', '')
            qty = item.get('quantity', '')
            chars = item.get('characteristics', '')
            brand = item.get('brand')

            line_parts = [f"<b>{name}</b>"]
            if qty:
                line_parts.append(f"— {qty}")
            lines.append(f"• {' '.join(line_parts)}")
            if chars:
                lines.append(f"    {chars[:150]}")
            if brand:
                lines.append(f"    Бренд: {brand}")
        lines.append("")
    else:
        specs = extraction.get('technical_specs', {})
        if specs.get('main_items'):
            lines.append("📦 <b>Позиции:</b>")
            for item in specs['main_items'][:5]:
                lines.append(f"• {item}")
            if specs.get('quantities'):
                lines.append(f"• Кол-во: {specs['quantities']}")
            lines.append("")

    # Сроки
    deadlines = extraction.get('deadlines', {})
    if not submission:
        submission = deadlines.get('submission_deadline')
    has_deadlines = any([
        deadlines.get('execution_days'),
        deadlines.get('execution_description'),
        deadlines.get('delivery_address'),
    ])
    if has_deadlines:
        lines.append("📅 <b>Сроки исполнения:</b>")
        if deadlines.get('execution_days'):
            lines.append(f"• Исполнение: {deadlines['execution_days']} дней")
        if deadlines.get('execution_description'):
            lines.append(f"• {deadlines['execution_description'][:100]}")
        if deadlines.get('delivery_address'):
            lines.append(f"• Адрес поставки: {deadlines['delivery_address'][:120]}")
        if not extraction.get('submission_deadline') and deadlines.get('submission_deadline'):
            lines.append(f"• Подача заявок до: <b>{deadlines['submission_deadline']}</b>")
        lines.append("")

    # Требования
    req = extraction.get('requirements', {})
    if any([req.get('licenses'), req.get('experience_years'), req.get('sro_required')]):
        lines.append("⚠️ <b>Требования к участнику:</b>")
        if req.get('licenses'):
            lines.append(f"• Лицензии: {', '.join(req['licenses'])}")
        if req.get('experience_years'):
            lines.append(f"• Опыт: от {req['experience_years']} лет")
        if req.get('sro_required'):
            lines.append("• Членство в СРО: требуется")
        lines.append("")

    # Обеспечение
    sec = extraction.get('contract_security', {})
    if any([sec.get('application_security_percent'), sec.get('contract_security_percent')]):
        lines.append("💳 <b>Обеспечение:</b>")
        if sec.get('application_security_percent'):
            lines.append(f"• Заявка: {sec['application_security_percent']}%")
        if sec.get('contract_security_percent'):
            lines.append(f"• Контракт: {sec['contract_security_percent']}%")
        if sec.get('bank_guarantee_allowed'):
            lines.append("• Банковская гарантия: допускается")
        lines.append("")

    # Оплата
    pay = extraction.get('payment_terms', {})
    if any([pay.get('advance_percent'), pay.get('payment_deadline_days')]):
        lines.append("💰 <b>Оплата:</b>")
        if pay.get('advance_percent'):
            lines.append(f"• Аванс: {pay['advance_percent']}%")
        if pay.get('payment_deadline_days'):
            lines.append(f"• Срок оплаты: {pay['payment_deadline_days']} дней")
        lines.append("")

    # Риски
    risks = extraction.get('risks', [])
    if risks:
        lines.append("🚩 <b>Риски:</b>")
        for risk in risks[:5]:
            icon = _classify_red_flag(risk)
            lines.append(f"• {icon} {risk}")
        lines.append("")

    # Резюме
    if extraction.get('summary'):
        lines.append(f"📝 <b>Резюме:</b> {extraction['summary']}")

    return "\n".join(lines)


# Singleton instance
_extractor_instance: Optional[TenderDocumentExtractor] = None


def get_document_extractor() -> TenderDocumentExtractor:
    """Получить singleton экземпляр экстрактора."""
    global _extractor_instance
    if _extractor_instance is None:
        _extractor_instance = TenderDocumentExtractor()
    return _extractor_instance


async def extract_tender_documentation(
    document_text: str,
    subscription_tier: str = 'trial',
    tender_info: Optional[Dict[str, Any]] = None
) -> Tuple[Dict[str, Any], bool]:
    """
    Удобная функция для извлечения данных из документации.

    Returns:
        Tuple[Dict, bool]: (данные, is_ai_extracted)
    """
    extractor = get_document_extractor()
    return await extractor.extract_from_text(document_text, subscription_tier, tender_info)
