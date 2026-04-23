"""
JSON API для веб-кабинета.

Эндпоинты: профиль, тендеры, документы, фильтры, поиск, статистика, настройки.
"""

import io
import os
import logging
from datetime import datetime, timedelta
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
    active_only_param = request.query.get('active_only', 'true').lower()
    active_only = active_only_param not in ('false', '0', 'no')
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    filters = await db.get_user_filters(user['user_id'], active_only=active_only)
    return web.json_response({'filters': filters})


@require_auth
async def update_filter(request: web.Request) -> web.Response:
    """PUT/POST /cabinet/api/filters/:id — обновление фильтра."""
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
               'regions', 'law_type', 'is_active', 'tender_types', 'ai_intent'}
    filtered = {k: v for k, v in data.items() if k in allowed}

    await db.update_filter(filter_id, **filtered)
    return web.json_response({'ok': True})


# ============================================
# FILTERS CRUD (новые эндпоинты)
# ============================================

@require_auth
async def create_filter(request: web.Request) -> web.Response:
    """POST /cabinet/api/filters/create — создание нового фильтра."""
    user = request['user']
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    name = (data.get('name') or '').strip()
    if not name:
        return web.json_response({'error': 'Name is required'}, status=400)

    keywords = data.get('keywords', [])
    if isinstance(keywords, str):
        keywords = [k.strip() for k in keywords.split(',') if k.strip()]

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    # Проверяем лимит фильтров
    user_info = await db.get_user_by_telegram_id(user['telegram_id'])
    filters_limit = user_info.get('filters_limit', 3) if user_info else 3
    existing = await db.get_user_filters(user['user_id'], active_only=False)
    active_count = sum(1 for f in existing if f.get('is_active') and not f.get('deleted_at'))
    if active_count >= filters_limit:
        return web.json_response({'error': f'Достигнут лимит фильтров ({filters_limit})'}, status=400)

    exclude_kw = data.get('exclude_keywords', [])
    if isinstance(exclude_kw, str):
        exclude_kw = [k.strip() for k in exclude_kw.split(',') if k.strip()]

    filter_id = await db.create_filter(
        user_id=user['user_id'],
        name=name,
        keywords=keywords,
        exclude_keywords=exclude_kw,
        price_min=data.get('price_min'),
        price_max=data.get('price_max'),
        regions=data.get('regions', []),
        law_type=data.get('law_type'),
        tender_types=data.get('tender_types', []),
        is_active=True,
    )

    # Сохраняем ai_intent если передан
    if data.get('ai_intent') and filter_id:
        await db.update_filter(filter_id, ai_intent=data['ai_intent'])

    return web.json_response({'ok': True, 'filter_id': filter_id})


@require_auth
async def delete_filter(request: web.Request) -> web.Response:
    """DELETE /cabinet/api/filters/:id — мягкое удаление фильтра."""
    user = request['user']
    filter_id = int(request.match_info['id'])

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('user_id') != user['user_id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    await db.delete_filter(filter_id)
    return web.json_response({'ok': True})


@require_auth
async def toggle_filter(request: web.Request) -> web.Response:
    """POST /cabinet/api/filters/:id/toggle — переключить is_active."""
    user = request['user']
    filter_id = int(request.match_info['id'])

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    filter_data = await db.get_filter_by_id(filter_id)
    if not filter_data or filter_data.get('user_id') != user['user_id']:
        return web.json_response({'error': 'Filter not found'}, status=404)

    new_state = not filter_data.get('is_active', True)
    await db.update_filter(filter_id, is_active=new_state)
    return web.json_response({'ok': True, 'is_active': new_state})


# ============================================
# REGIONS API
# ============================================

@require_auth
async def get_regions(request: web.Request) -> web.Response:
    """GET /cabinet/api/regions — список регионов по округам."""
    from tender_sniper.regions import FEDERAL_DISTRICTS
    result = []
    for district_name, district_data in FEDERAL_DISTRICTS.items():
        result.append({
            'district': district_name,
            'code': district_data.get('code', ''),
            'regions': district_data.get('regions', []),
        })
    return web.json_response({'districts': result})


# ============================================
# INSTANT SEARCH API
# ============================================

@require_auth
async def search_tenders(request: web.Request) -> web.Response:
    """GET /cabinet/api/search?q=...&region=...&price_min=N&price_max=N&law=44&limit=25"""
    user = request['user']
    q = request.query.get('q', '').strip()
    if not q:
        return web.json_response({'error': 'Query is required'}, status=400)

    region = request.query.get('region', '').strip()
    price_min = request.query.get('price_min')
    price_max = request.query.get('price_max')
    law = request.query.get('law', '')
    limit = min(int(request.query.get('limit', '25')), 50)

    try:
        from tender_sniper.instant_search import InstantSearch
        searcher = InstantSearch()

        # Формируем фильтр для поиска
        import json as _json
        filter_data = {
            'id': 0,
            'user_id': user['user_id'],
            'name': 'web_search',
            'keywords': _json.dumps(q.split()),
            'exclude_keywords': _json.dumps([]),
            'regions': _json.dumps([region] if region else []),
            'law_type': law if law in ('44', '223') else None,
            'price_min': float(price_min) if price_min else None,
            'price_max': float(price_max) if price_max else None,
            'tender_types': _json.dumps([]),
            'is_active': True,
            'ai_intent': None,
            'subscription_tier': 'starter',
        }

        results = await searcher.search_by_filter(filter_data, max_tenders=limit)

        matches = results.get('matches', []) if isinstance(results, dict) else results
        tenders = []
        for r in matches[:limit]:
            tenders.append({
                'number': r.get('number', ''),
                'name': r.get('name', ''),
                'price': r.get('price'),
                'customer_name': r.get('customer_name', ''),
                'region': r.get('region', ''),
                'deadline': r.get('deadline', ''),
                'url': r.get('url', ''),
                'score': r.get('match_score', 0),
                'law_type': r.get('law_type', ''),
            })

        return web.json_response({'tenders': tenders, 'total': len(tenders)})

    except Exception as e:
        logger.error(f"Search error: {e}", exc_info=True)
        return web.json_response({'error': str(e)}, status=500)


# ============================================
# STATS API
# ============================================

@require_auth
async def get_stats(request: web.Request) -> web.Response:
    """GET /cabinet/api/stats — статистика пользователя."""
    user = request['user']
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    stats = await db.get_user_stats(user['user_id'])

    # Последние 10 тендеров
    recent = await db.get_user_tenders(user['user_id'], limit=10)

    # Топ фильтры: фильтры с количеством уведомлений
    all_tenders = await db.get_user_tenders(user['user_id'], limit=500)
    filter_counts: Dict[str, int] = {}
    for t in all_tenders:
        fn = t.get('filter_name') or 'Без фильтра'
        filter_counts[fn] = filter_counts.get(fn, 0) + 1
    top_filters = sorted(filter_counts.items(), key=lambda x: -x[1])[:5]

    return web.json_response({
        **stats,
        'recent_tenders': recent[:10],
        'top_filters': [{'name': k, 'count': v} for k, v in top_filters],
    })


# ============================================
# TENDER ACTIONS
# ============================================

@require_auth
async def tender_feedback(request: web.Request) -> web.Response:
    """POST /cabinet/api/tenders/:number/feedback — интересно/пропустить."""
    user = request['user']
    tender_number = request.match_info['tender_number']

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    feedback_type = data.get('type', '')
    if feedback_type not in ('interesting', 'skip'):
        return web.json_response({'error': 'type must be interesting or skip'}, status=400)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    await db.save_user_feedback(
        user_id=user['user_id'],
        tender_number=tender_number,
        feedback_type=feedback_type,
    )
    return web.json_response({'ok': True})


@require_auth
async def export_to_sheets(request: web.Request) -> web.Response:
    """POST /cabinet/api/tenders/:number/export-sheets — экспорт в Google Sheets."""
    user = request['user']
    tender_number = request.match_info['tender_number']

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    # Проверяем что GSheets настроен
    sheets_config = await db.get_google_sheets_config(user['user_id'])
    if not sheets_config:
        return web.json_response({'error': 'Google Sheets не настроен. Настройте в боте: /settings → Google Sheets'}, status=400)

    # Находим тендер в уведомлениях пользователя
    tenders = await db.get_user_tenders(user['user_id'], limit=500)
    tender_data = next((t for t in tenders if t.get('number') == tender_number), None)

    if not tender_data:
        return web.json_response({'error': 'Тендер не найден'}, status=404)

    try:
        from tender_sniper.google_sheets_sync import GoogleSheetsSync
        sync = GoogleSheetsSync()
        spreadsheet_id = sheets_config.get('spreadsheet_id', '')
        sheet_name = sheets_config.get('sheet_name')
        columns = sheets_config.get('columns') or []
        match_data = {'filter_name': tender_data.get('filter_name', '')}
        ok = await sync.append_tender(
            spreadsheet_id=spreadsheet_id,
            tender_data=tender_data,
            match_data=match_data,
            columns=columns,
            sheet_name=sheet_name,
        )
        if ok:
            return web.json_response({'ok': True})
        else:
            return web.json_response({'error': 'Ошибка экспорта в таблицу'}, status=500)
    except Exception as e:
        logger.error(f"Sheets export error: {e}", exc_info=True)
        return web.json_response({'error': 'Ошибка экспорта: ' + str(e)}, status=500)


# ============================================
# SETTINGS API
# ============================================

@require_auth
async def get_settings(request: web.Request) -> web.Response:
    """GET /cabinet/api/settings — настройки пользователя."""
    user = request['user']
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    user_info = await db.get_user_by_telegram_id(user['telegram_id'])
    if not user_info:
        return web.json_response({'error': 'User not found'}, status=404)

    sub_info = await db.get_user_subscription_info(user['telegram_id']) or {}
    data = user_info.get('data') or {}

    sheets_config = await db.get_google_sheets_config(user['user_id'])

    trial_expires = sub_info.get('trial_expires_at')
    if hasattr(trial_expires, 'isoformat'):
        trial_expires = trial_expires.isoformat()

    return web.json_response({
        'monitoring_paused_until': data.get('monitoring_paused_until'),
        'notifications_enabled': user_info.get('notifications_enabled', True),
        'quiet_hours_enabled': data.get('quiet_hours_enabled', False),
        'quiet_hours_start': data.get('quiet_hours_start', 22),
        'quiet_hours_end': data.get('quiet_hours_end', 8),
        'subscription_tier': user_info.get('subscription_tier', 'trial'),
        'trial_expires_at': trial_expires,
        'filters_limit': user_info.get('filters_limit', 3),
        'notifications_limit': user_info.get('notifications_limit', 15),
        'sheets_configured': bool(sheets_config),
        'bitrix24_webhook': data.get('bitrix24_webhook', ''),
        'bitrix24_enabled': bool(data.get('bitrix24_enabled', False)),
    })


@require_auth
async def save_settings(request: web.Request) -> web.Response:
    """POST /cabinet/api/settings — обновление настроек."""
    user = request['user']
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    user_info = await db.get_user_by_telegram_id(user['telegram_id'])
    if not user_info:
        return web.json_response({'error': 'User not found'}, status=404)

    current_data = user_info.get('data') or {}

    # Обновляем notifications_enabled напрямую в БД
    if 'notifications_enabled' in data:
        enabled = bool(data['notifications_enabled'])
        await db.set_monitoring_status(user['telegram_id'], enabled)

    # Обновляем данные в JSON поле data
    updates = {}
    if 'quiet_hours_enabled' in data:
        updates['quiet_hours_enabled'] = bool(data['quiet_hours_enabled'])
    if 'quiet_hours_start' in data:
        updates['quiet_hours_start'] = int(data['quiet_hours_start'])
    if 'quiet_hours_end' in data:
        updates['quiet_hours_end'] = int(data['quiet_hours_end'])

    # Пауза мониторинга
    if 'monitoring_pause' in data:
        pause = data['monitoring_pause']
        if pause == '24h':
            updates['monitoring_paused_until'] = (datetime.utcnow() + timedelta(hours=24)).isoformat()
        elif pause == 'forever':
            updates['monitoring_paused_until'] = '9999-12-31T00:00:00'
        elif pause == 'resume':
            updates['monitoring_paused_until'] = None

    if updates:
        new_data = {**current_data, **updates}
        await db.update_user_json_data(user['user_id'], new_data)

    return web.json_response({'ok': True})


# ============================================
# TENDER-GPT API
# ============================================

@require_auth
async def api_gpt_chat(request: web.Request) -> web.Response:
    """POST /cabinet/api/gpt/chat — отправить сообщение в Tender-GPT."""
    user = request['user']

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    message = (data.get('message') or '').strip()
    if not message:
        return web.json_response({'error': 'Пустое сообщение'}, status=400)

    try:
        from tender_sniper.tender_gpt.service import TenderGPTService
        service = TenderGPTService()

        result = await service.chat(
            telegram_id=user['telegram_id'],
            user_id=user['user_id'],
            user_message=message,
        )

        return web.json_response(result)
    except Exception as e:
        logger.error(f"GPT chat error for user {user['user_id']}: {e}", exc_info=True)
        return web.json_response({'error': 'Ошибка AI-сервиса. Попробуйте позже.'}, status=500)


# ============================================
# SUBSCRIPTION API
# ============================================

@require_auth
async def api_subscription_info(request: web.Request) -> web.Response:
    """GET /cabinet/api/subscription — информация о текущей подписке."""
    user = request['user']

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    sub_info = await db.get_user_subscription_info(user['telegram_id']) or {}
    user_info = await db.get_user_by_telegram_id(user['telegram_id']) or {}
    user_data = user_info.get('data') or {}

    tier = sub_info.get('subscription_tier') or user_info.get('subscription_tier', 'trial')
    trial_expires = sub_info.get('trial_expires_at')

    # Calculate days left
    days_left = 0
    expires_at = None
    if trial_expires:
        if hasattr(trial_expires, 'isoformat'):
            expires_at = trial_expires.isoformat()
            days_left = max(0, (trial_expires - datetime.utcnow()).days)
        else:
            try:
                exp_dt = datetime.fromisoformat(str(trial_expires))
                expires_at = exp_dt.isoformat()
                days_left = max(0, (exp_dt - datetime.utcnow()).days)
            except Exception:
                expires_at = str(trial_expires)

    # Check if first payment
    is_first = not user_data.get('has_paid_before', False)

    return web.json_response({
        'tier': tier,
        'expires_at': expires_at,
        'days_left': days_left,
        'filters_limit': sub_info.get('filters_limit') or user_info.get('filters_limit', 3),
        'notifications_limit': sub_info.get('notifications_limit') or user_info.get('notifications_limit', 15),
        'is_first_payment': is_first,
    })


@require_auth
async def api_subscription_pay(request: web.Request) -> web.Response:
    """POST /cabinet/api/subscription/pay — создать платёж YooKassa."""
    user = request['user']

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    tier = data.get('tier', 'starter')
    months = int(data.get('months', 1))

    if tier not in ('starter', 'pro', 'premium'):
        return web.json_response({'error': 'Неверный тариф'}, status=400)
    if months not in (1, 3, 6):
        return web.json_response({'error': 'Неверный период'}, status=400)

    try:
        from bot.handlers.subscriptions import calculate_price
        from tender_sniper.payments import get_yookassa_client
        from tender_sniper.database import get_sniper_db

        db = await get_sniper_db()
        user_info = await db.get_user_by_telegram_id(user['telegram_id'])
        user_data = (user_info.get('data') or {}) if user_info else {}
        is_first = not user_data.get('has_paid_before', False)

        price_info = calculate_price(tier, months, is_first_payment=is_first)

        client = get_yookassa_client()
        if not client.is_configured:
            return web.json_response({'error': 'Платёжная система не настроена'}, status=500)

        result = client.create_payment(
            telegram_id=user['telegram_id'],
            tier=tier,
            amount=price_info['final_price'],
            days=price_info['days'],
            description=f"Подписка {tier} на {price_info['label']}",
            return_url=f"{request.scheme}://{request.host}/cabinet/subscription",
        )

        if result.get('error'):
            return web.json_response({'error': result['error']}, status=500)

        return web.json_response({
            'url': result.get('url'),
            'amount': price_info['final_price'],
        })
    except Exception as e:
        logger.error(f"Payment creation error for user {user['user_id']}: {e}", exc_info=True)
        return web.json_response({'error': 'Ошибка создания платежа'}, status=500)


# ============================================
# CALENDAR API
# ============================================

@require_auth
async def api_calendar(request: web.Request) -> web.Response:
    """GET /cabinet/api/calendar?month=2026-03 — тендеры с дедлайнами по дням."""
    user = request['user']

    month_str = request.query.get('month', '')
    if not month_str:
        month_str = datetime.utcnow().strftime('%Y-%m')

    try:
        parts = month_str.split('-')
        year = int(parts[0])
        month = int(parts[1])
    except (ValueError, IndexError):
        return web.json_response({'error': 'Invalid month format (YYYY-MM)'}, status=400)

    # Calculate month boundaries
    month_start = datetime(year, month, 1)
    if month == 12:
        month_end = datetime(year + 1, 1, 1)
    else:
        month_end = datetime(year, month + 1, 1)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()

    # Get user tenders with deadlines (last 500 for coverage)
    all_tenders = await db.get_user_tenders(user['user_id'], limit=500)

    # Group by deadline date within the requested month
    days_map = {}  # date_str -> list of tenders
    for t in all_tenders:
        deadline = t.get('submission_deadline')
        if not deadline:
            continue

        try:
            if isinstance(deadline, str):
                dl_dt = datetime.fromisoformat(deadline)
            else:
                dl_dt = deadline
        except Exception:
            continue

        if dl_dt < month_start or dl_dt >= month_end:
            continue

        date_str = dl_dt.strftime('%Y-%m-%d')
        if date_str not in days_map:
            days_map[date_str] = []

        deadline_time = dl_dt.strftime('%H:%M') if dl_dt.hour or dl_dt.minute else ''

        days_map[date_str].append({
            'name': t.get('name', ''),
            'number': t.get('number', ''),
            'price': t.get('price'),
            'url': t.get('url', ''),
            'deadline_time': deadline_time,
        })

    # Build response
    days = []
    for date_str in sorted(days_map.keys()):
        tenders = days_map[date_str]
        days.append({
            'date': date_str,
            'count': len(tenders),
            'tenders': tenders,
        })

    return web.json_response({'days': days, 'month': month_str})


# ============================================
# BITRIX24 INTEGRATION API
# ============================================

@require_auth
async def save_bitrix24_settings(request: web.Request) -> web.Response:
    """POST /cabinet/api/settings/bitrix24 — сохранить webhook + enabled-флаг."""
    user = request['user']
    try:
        payload = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    webhook_url = (payload.get('webhook_url') or '').strip()
    enabled = bool(payload.get('enabled', True))

    if webhook_url:
        from bot.handlers.bitrix24 import validate_bitrix24_webhook
        ok, msg = await validate_bitrix24_webhook(webhook_url)
        if not ok:
            return web.json_response({'error': f'Webhook невалиден: {msg}'}, status=400)

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    user_info = await db.get_user_by_telegram_id(user['telegram_id'])
    if not user_info:
        return web.json_response({'error': 'User not found'}, status=404)

    current = user_info.get('data') or {}
    current['bitrix24_webhook'] = webhook_url
    current['bitrix24_enabled'] = enabled
    await db.update_user_json_data(user['user_id'], current)

    return web.json_response({'ok': True})


@require_auth
async def test_bitrix24_settings(request: web.Request) -> web.Response:
    """POST /cabinet/api/settings/bitrix24/test — тест сохранённого webhook."""
    user = request['user']
    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    user_info = await db.get_user_by_telegram_id(user['telegram_id']) or {}
    webhook = (user_info.get('data') or {}).get('bitrix24_webhook', '')
    if not webhook:
        return web.json_response({'error': 'Webhook не настроен'}, status=400)

    from bot.handlers.bitrix24 import validate_bitrix24_webhook
    ok, msg = await validate_bitrix24_webhook(webhook)
    if ok:
        return web.json_response({'ok': True, 'message': msg or 'Соединение успешно'})
    return web.json_response({'error': msg or 'Не удалось подключиться'}, status=400)


@require_auth
async def export_tender_to_bitrix24(request: web.Request) -> web.Response:
    """POST /cabinet/api/tenders/{tender_number}/bitrix24 — создать сделку из тендера."""
    user = request['user']
    tender_number = request.match_info['tender_number']

    from tender_sniper.database import get_sniper_db
    db = await get_sniper_db()
    user_info = await db.get_user_by_telegram_id(user['telegram_id']) or {}
    user_data = user_info.get('data') or {}

    webhook = user_data.get('bitrix24_webhook', '')
    enabled = user_data.get('bitrix24_enabled', False)
    if not webhook or not enabled:
        return web.json_response(
            {'error': 'Битрикс24 не настроен. Зайдите в Настройки → Интеграции.'},
            status=400,
        )

    tenders = await db.get_user_tenders(user['user_id'], limit=500)
    tender = next((t for t in tenders if t.get('number') == tender_number), None)
    if not tender:
        return web.json_response({'error': 'Тендер не найден в вашей истории'}, status=404)

    from bot.handlers.bitrix24 import (
        BITRIX24_FULL_ACCESS_USERS,
        create_bitrix24_deal,
        create_simple_bitrix24_deal,
    )

    if user['user_id'] in BITRIX24_FULL_ACCESS_USERS:
        deal_id = await create_bitrix24_deal(
            webhook_url=webhook,
            tender_number=tender.get('number', ''),
            tender_name=tender.get('name', ''),
            tender_price=tender.get('price'),
            tender_url=tender.get('url', ''),
            tender_region=tender.get('region', '') or '',
            tender_customer=tender.get('customer_name', '') or '',
            filter_name=tender.get('filter_name', '') or '',
            submission_deadline=tender.get('submission_deadline', '') or '',
            law_type=tender.get('law_type', '') or '',
        )
    else:
        deal_id = await create_simple_bitrix24_deal(
            webhook_url=webhook,
            tender_number=tender.get('number', ''),
            tender_name=tender.get('name', ''),
            tender_price=tender.get('price'),
            tender_url=tender.get('url', ''),
            tender_customer=tender.get('customer_name', '') or '',
            tender_region=tender.get('region', '') or '',
            submission_deadline=tender.get('submission_deadline', '') or '',
        )

    if deal_id:
        return web.json_response({'ok': True, 'deal_id': deal_id})
    return web.json_response({'error': 'Не удалось создать сделку. Проверьте webhook.'}, status=500)
