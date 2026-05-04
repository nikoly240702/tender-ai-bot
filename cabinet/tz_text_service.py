"""Получение полного текста ТЗ для карточки pipeline.

Приоритет источников:
1. Документация тендера с zakupki.gov.ru (скачивается на лету) — основной путь
2. Файлы загруженные вручную в карточку (pipeline_card_files) — fallback
   когда zakupki недоступен или вернул пустой набор
3. card.ai_summary — финальный fallback
4. Только название тендера — крайний случай

Результат кэшируется в card.data.tz_full = {text, source, fetched_at,
expires_at, files_used} на 7 дней. Для принудительного обновления —
force=True.

Используется в supplier_request_service для AI-оценки и clean ТЗ.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from database import (
    DatabaseSession, PipelineCard, PipelineCardFile,
)

logger = logging.getLogger(__name__)


CACHE_TTL_DAYS = 7
DOWNLOAD_DIR = Path('/tmp/tender_downloads')
MAX_TEXT_CHARS = 30000  # обрезаем чтобы AI-prompt не разрастался
SUPPORTED_EXTENSIONS = {'.docx', '.doc', '.pdf', '.xlsx', '.xls', '.rtf', '.txt', '.csv'}


def _now() -> datetime:
    return datetime.utcnow()


# ============================================
# Source 1: extract from uploaded files
# ============================================

def _extract_from_local_file(file_path: str) -> str:
    """Sync функция (для asyncio.to_thread). Читает файл через TextExtractor."""
    try:
        from src.document_processor.text_extractor import TextExtractor
        result = TextExtractor.extract_text(file_path)
        if isinstance(result, dict):
            return result.get('text') or result.get('content') or ''
        return str(result or '')
    except Exception as e:
        logger.warning(f'TextExtractor failed for {file_path}: {e}')
        return ''


async def _get_text_from_card_files(card_id: int) -> Tuple[str, List[Dict]]:
    """Возвращает (combined_text, files_used).
    Берёт все файлы карточки с подходящим расширением."""
    async with DatabaseSession() as session:
        result = await session.execute(
            select(PipelineCardFile).where(PipelineCardFile.card_id == card_id)
            .order_by(PipelineCardFile.uploaded_at.desc())
        )
        files = result.scalars().all()

    if not files:
        return '', []

    parts: List[str] = []
    files_used: List[Dict] = []
    for f in files:
        path = f.path
        if not path or not Path(path).exists():
            continue
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            continue
        text = await asyncio.to_thread(_extract_from_local_file, path)
        if not text or len(text.strip()) < 50:
            continue
        parts.append(f'=== {f.filename} ===\n{text.strip()}')
        files_used.append({'id': f.id, 'filename': f.filename, 'chars': len(text)})

    combined = '\n\n'.join(parts)
    return combined[:MAX_TEXT_CHARS], files_used


# ============================================
# Source 2: download from zakupki
# ============================================

_PRIORITY_TYPES = ('tz', 'description', 'other')  # ТЗ важнее проекта контракта
_DOC_TITLE_PRIORITY_KEYWORDS = (
    'описание объекта закупки', 'техническое задание', 'тз ',
    'характеристик', 'спецификац',
)
_SKIP_TITLE_KEYWORDS = (
    'обоснование нмцк', 'обоснование начальной', 'требования к содержанию',
    'требования к составу заявки',
)


def _doc_priority_score(doc: Dict) -> int:
    """Чем меньше — тем раньше скачиваем. ТЗ-подобные документы первыми."""
    title = (doc.get('title') or '').lower()
    if any(kw in title for kw in _SKIP_TITLE_KEYWORDS):
        return 9999  # skip-кандидаты в конец
    if any(kw in title for kw in _DOC_TITLE_PRIORITY_KEYWORDS):
        return 0
    if doc.get('type') == 'contract':
        return 50  # проект контракта — после ТЗ
    return 10


def _download_and_extract_from_zakupki(tender_url: str, tender_number: str) -> Tuple[str, List[str]]:
    """Sync функция. Качает документы тендера с zakupki, извлекает текст.
    Возвращает (combined_text, list_of_filenames).
    Приоритет файлов: ТЗ/описание объекта закупки → проект контракта → остальное.
    """
    logger.info(f'[tz] zakupki download start: tender={tender_number}')
    try:
        from src.parsers.zakupki_document_downloader import ZakupkiDocumentDownloader
        from src.document_processor.text_extractor import TextExtractor
    except Exception as e:
        logger.error(f'[tz] cannot import downloader/extractor: {e}')
        return '', []

    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    downloader = ZakupkiDocumentDownloader(download_dir=DOWNLOAD_DIR)

    try:
        result = downloader.download_documents(tender_url, tender_number)
    except Exception as e:
        logger.warning(f'[tz] zakupki download_documents failed: {e}', exc_info=True)
        return '', []

    files = result.get('files') if isinstance(result, dict) else None
    if not files:
        logger.warning(f'[tz] zakupki returned 0 files for {tender_number}')
        return '', []

    # Сортируем по приоритету так чтобы ТЗ оказался впереди
    sorted_files = sorted(files, key=lambda f: _doc_priority_score(f))
    logger.info(
        f'[tz] zakupki got {len(files)} files, priority order: '
        + ', '.join((f.get('title') or '?')[:40] for f in sorted_files[:5])
    )

    parts: List[str] = []
    filenames: List[str] = []
    for d in sorted_files[:10]:
        path = d.get('path') or d.get('local_path') or d.get('file_path')
        if not path or not Path(path).exists():
            continue
        ext = Path(path).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            logger.debug(f'[tz] skip unsupported {path}')
            continue
        try:
            extracted = TextExtractor.extract_text(path)
            text = extracted.get('text') if isinstance(extracted, dict) else str(extracted or '')
        except Exception as e:
            logger.warning(f'[tz] TextExtractor failed for {path}: {e}')
            continue
        if not text or len(text.strip()) < 50:
            continue
        title = d.get('title') or Path(path).name
        parts.append(f'=== {title} ===\n{text.strip()}')
        filenames.append(title)

    combined = '\n\n'.join(parts)
    logger.info(f'[tz] zakupki done: {len(filenames)} files extracted, total {len(combined)} chars')
    return combined[:MAX_TEXT_CHARS], filenames


# ============================================
# Public API
# ============================================

async def get_full_tz_text(card_id: int, company_id: int,
                           force: bool = False) -> Dict[str, Any]:
    """Главная функция. Возвращает {ok, text, source, files_used?, cached?}.

    source ∈ {'card_files', 'zakupki', 'cache', 'fallback_summary', 'name_only'}.
    """
    async with DatabaseSession() as session:
        card = await session.scalar(
            select(PipelineCard).where(
                PipelineCard.id == card_id,
                PipelineCard.company_id == company_id,
            )
        )
        if not card:
            return {'ok': False, 'error': 'Карточка не найдена'}

        data = dict(card.data or {})
        cached = data.get('tz_full')
        if cached and not force:
            try:
                expires = datetime.fromisoformat(cached.get('expires_at', ''))
                if expires > _now() and cached.get('text'):
                    return {
                        'ok': True,
                        'text': cached['text'],
                        'source': 'cache',
                        'cached_source': cached.get('source'),
                        'files_used': cached.get('files_used', []),
                    }
            except Exception:
                pass

        tender_url = (card.data or {}).get('url') or ''
        tender_number = card.tender_number

    text = ''
    files_used: List[Dict] = []
    source: Optional[str] = None

    # 1. Try zakupki download FIRST (основной путь)
    if tender_url and tender_number:
        zk_text, filenames = await asyncio.to_thread(
            _download_and_extract_from_zakupki, tender_url, tender_number,
        )
        if zk_text and len(zk_text.strip()) >= 200:
            text = zk_text
            source = 'zakupki'
            files_used = [{'filename': f, 'source': 'zakupki'} for f in filenames]

    # 2. Fallback: файлы загруженные в карточку
    if not text:
        cf_text, cf_files = await _get_text_from_card_files(card_id)
        if cf_text and len(cf_text.strip()) >= 200:
            text = cf_text
            source = 'card_files'
            files_used = cf_files

    if not text or len(text.strip()) < 200:
        # Fallback: ai_summary, потом просто name
        async with DatabaseSession() as session:
            card = await session.get(PipelineCard, card_id)
            if not card:
                return {'ok': False, 'error': 'Карточка пропала'}
            summary = (card.ai_summary or '').strip()
            if summary:
                return {
                    'ok': True,
                    'text': summary,
                    'source': 'fallback_summary',
                    'files_used': [],
                    'note': 'Полная документация недоступна — используем AI-summary как fallback',
                }
            data = card.data or {}
            name = data.get('name') or f'Тендер {card.tender_number}'
            return {
                'ok': True,
                'text': name,
                'source': 'name_only',
                'files_used': [],
                'note': 'Доступно только название тендера. Загрузите ТЗ в файлы карточки.',
            }

    # 3. Cache result
    fetched = _now()
    cache_block = {
        'text': text,
        'source': source,
        'files_used': files_used,
        'fetched_at': fetched.isoformat(),
        'expires_at': (fetched + timedelta(days=CACHE_TTL_DAYS)).isoformat(),
    }
    async with DatabaseSession() as session:
        card = await session.get(PipelineCard, card_id)
        if card:
            data = dict(card.data or {})
            data['tz_full'] = cache_block
            card.data = data
            flag_modified(card, 'data')
            await session.commit()

    return {
        'ok': True,
        'text': text,
        'source': source,
        'files_used': files_used,
        'cached': False,
    }
