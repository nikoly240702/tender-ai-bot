"""
JSON API для веб-кабинета.

Эндпоинты: профиль, тендеры, документы, фильтры.
"""

import io
import logging
from aiohttp import web
from typing import Dict, Any

from .auth import require_auth

logger = logging.getLogger(__name__)


# ============================================
# PROFILE API
# ============================================

@require_auth
async def get_profile(request: web.Request) -> web.Response:
    """GET /cabinet/api/profile — получение профиля компании."""
    user = request['user']
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    profile = await db.get_company_profile(user['user_id'])
    return web.json_response({'profile': profile})


@require_auth
async def save_profile(request: web.Request) -> web.Response:
    """POST /cabinet/api/profile — сохранение профиля компании."""
    user = request['user']
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    # Фильтруем допустимые поля
    allowed_fields = {
        'company_name', 'company_name_short', 'legal_form', 'inn', 'kpp', 'ogrn',
        'legal_address', 'actual_address', 'postal_address',
        'director_name', 'director_position', 'director_basis',
        'phone', 'email', 'website',
        'bank_name', 'bank_bik', 'bank_account', 'bank_corr_account',
        'smp_status', 'licenses_text', 'experience_description',
    }
    filtered = {k: v for k, v in data.items() if k in allowed_fields}

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    profile_id = await db.upsert_company_profile(user['user_id'], filtered)
    await db.check_profile_completeness(user['user_id'])

    return web.json_response({'ok': True, 'profile_id': profile_id})


# ============================================
# TENDERS API
# ============================================

@require_auth
async def get_tenders(request: web.Request) -> web.Response:
    """GET /cabinet/api/tenders — история тендеров с пагинацией."""
    user = request['user']
    page = int(request.query.get('page', '1'))
    limit = min(int(request.query.get('limit', '50')), 100)
    offset = (page - 1) * limit

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    tenders = await db.get_user_tenders(user['user_id'], limit=limit + offset)

    # Ручная пагинация (adapter возвращает лимитированный список)
    page_tenders = tenders[offset:offset + limit] if offset < len(tenders) else []

    return web.json_response({
        'tenders': page_tenders,
        'total': len(tenders),
        'page': page,
        'limit': limit,
    })


# ============================================
# DOCUMENTS API
# ============================================

@require_auth
async def get_documents(request: web.Request) -> web.Response:
    """GET /cabinet/api/documents — список сгенерированных документов."""
    user = request['user']
    tender_number = request.query.get('tender_number')

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    documents = await db.get_user_documents(user['user_id'], tender_number=tender_number)
    return web.json_response({'documents': documents})


@require_auth
async def download_document(request: web.Request) -> web.Response:
    """GET /cabinet/api/documents/:id/download — скачивание документа (регенерация на лету)."""
    user = request['user']
    doc_id = int(request.match_info['id'])

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    doc = await db.get_document_by_id(doc_id)

    if not doc or doc['user_id'] != user['user_id']:
        return web.json_response({'error': 'Document not found'}, status=404)

    # Регенерируем документ на лету
    try:
        from tender_sniper.document_generator import DocumentGenerator

        # Получаем данные
        profile = await db.get_company_profile(user['user_id'])
        # Получаем тендер из уведомлений
        tenders = await db.get_user_tenders(user['user_id'])
        tender_data = None
        for t in tenders:
            if t['number'] == doc['tender_number']:
                tender_data = t
                break

        if not tender_data or not profile:
            return web.json_response({'error': 'Missing data for regeneration'}, status=400)

        generator = DocumentGenerator()
        context_tender = {
            'number': tender_data.get('number', ''),
            'name': tender_data.get('name', ''),
            'price': tender_data.get('price'),
            'url': tender_data.get('url', ''),
            'customer_name': tender_data.get('customer_name', ''),
            'region': tender_data.get('region', ''),
        }

        documents = await generator.generate_package(
            tender_data=context_tender,
            company_profile=profile,
            user_id=user['user_id'],
            ai_proposal_text=doc.get('ai_generated_content'),
        )

        # Находим нужный тип документа
        for doc_type, filename, doc_bytes in documents:
            if doc_type == doc['doc_type']:
                await db.increment_download_count(doc_id)
                return web.Response(
                    body=doc_bytes.read(),
                    content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={
                        'Content-Disposition': f'attachment; filename="{filename}"'
                    }
                )

        return web.json_response({'error': 'Document type not found'}, status=404)

    except Exception as e:
        logger.error(f"Error regenerating document {doc_id}: {e}", exc_info=True)
        return web.json_response({'error': 'Generation failed'}, status=500)


@require_auth
async def generate_documents(request: web.Request) -> web.Response:
    """POST /cabinet/api/documents/generate/:tender — генерация пакета для тендера."""
    user = request['user']
    tender_number = request.match_info['tender']

    from tender_sniper.database import get_sniper_db
    from tender_sniper.document_generator import DocumentGenerator
    from tender_sniper.document_generator.ai_proposal import AIProposalGenerator

    db = await get_sniper_db()
    profile = await db.get_company_profile(user['user_id'])

    if not profile or not profile.get('is_complete'):
        return web.json_response({'error': 'Profile not complete'}, status=400)

    # Получаем тендер
    tenders = await db.get_user_tenders(user['user_id'])
    tender_data = None
    for t in tenders:
        if t['number'] == tender_number:
            tender_data = t
            break

    if not tender_data:
        return web.json_response({'error': 'Tender not found'}, status=404)

    context_tender = {
        'number': tender_data.get('number', ''),
        'name': tender_data.get('name', ''),
        'price': tender_data.get('price'),
        'url': tender_data.get('url', ''),
        'customer_name': tender_data.get('customer_name', ''),
        'region': tender_data.get('region', ''),
    }

    # AI предложение
    ai_gen = AIProposalGenerator()
    ai_text = await ai_gen.generate_proposal_text(
        tender_name=context_tender['name'],
        company_profile=profile,
    )

    # Генерация
    generator = DocumentGenerator()
    documents = await generator.generate_package(
        tender_data=context_tender,
        company_profile=profile,
        user_id=user['user_id'],
        ai_proposal_text=ai_text,
    )

    # Сохраняем записи в БД
    doc_ids = []
    for doc_type, filename, _ in documents:
        doc_id = await db.save_generated_document(
            user_id=user['user_id'],
            tender_number=tender_number,
            doc_type=doc_type,
            doc_name=filename,
            status='ready',
            ai_content=ai_text if doc_type == 'proposal' else None,
        )
        doc_ids.append(doc_id)

    return web.json_response({'ok': True, 'document_ids': doc_ids})


# ============================================
# FILTERS API
# ============================================

@require_auth
async def get_filters(request: web.Request) -> web.Response:
    """GET /cabinet/api/filters — фильтры пользователя."""
    user = request['user']
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    filters = await db.get_user_filters(user['user_id'], active_only=True)
    return web.json_response({'filters': filters})


@require_auth
async def update_filter(request: web.Request) -> web.Response:
    """POST /cabinet/api/filters/:id — обновление фильтра."""
    user = request['user']
    filter_id = int(request.match_info['id'])

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    # Проверяем что фильтр принадлежит пользователю
    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('user_id') != user['user_id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    # Допустимые поля для обновления
    allowed = {'name', 'keywords', 'exclude_keywords', 'price_min', 'price_max',
               'regions', 'law_type', 'is_active'}
    filtered = {k: v for k, v in data.items() if k in allowed}

    await db.update_filter(filter_id, **filtered)
    return web.json_response({'ok': True})
