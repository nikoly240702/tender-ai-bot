"""
Telegram Mini App API для Tender Sniper.

Endpoints для просмотра тендеров и экспорта в Google Sheets.
Аутентификация через Telegram WebApp initData (HMAC-SHA256).
"""

import hashlib
import hmac
import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from sqlalchemy import select, update, and_
from sqlalchemy.orm import selectinload

from database import (
    SniperUser,
    SniperNotification,
    GoogleSheetsConfig,
    DatabaseSession,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webapp")

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


# ============================================
# TELEGRAM WEBAPP AUTH
# ============================================

def validate_telegram_webapp(init_data: str, bot_token: str) -> Optional[Dict[str, Any]]:
    """
    Валидирует initData из Telegram WebApp.
    Возвращает данные пользователя или None если невалидно.

    Алгоритм из документации Telegram:
    https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        parsed = urllib.parse.parse_qs(init_data)

        # Получаем hash из данных
        received_hash = parsed.get('hash', [''])[0]
        if not received_hash:
            return None

        # Собираем data-check-string (все поля кроме hash, отсортированные)
        data_pairs = []
        for key, values in parsed.items():
            if key != 'hash':
                data_pairs.append(f"{key}={values[0]}")
        data_pairs.sort()
        data_check_string = '\n'.join(data_pairs)

        # Вычисляем secret_key = HMAC-SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            b"WebAppData",
            bot_token.encode(),
            hashlib.sha256
        ).digest()

        # Вычисляем hash = HMAC-SHA256(secret_key, data_check_string)
        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            return None

        # Проверяем auth_date (не старше 1 часа)
        auth_date = int(parsed.get('auth_date', ['0'])[0])
        if datetime.utcnow().timestamp() - auth_date > 3600:
            return None

        # Извлекаем данные пользователя
        user_data = json.loads(parsed.get('user', ['{}'])[0])
        return user_data

    except Exception as e:
        logger.warning(f"WebApp auth error: {e}")
        return None


async def get_webapp_user(request: Request) -> Dict[str, Any]:
    """Извлекает и валидирует пользователя из WebApp запроса."""
    init_data = request.headers.get('X-Telegram-Init-Data', '')

    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")

    user_data = validate_telegram_webapp(init_data, TELEGRAM_BOT_TOKEN)
    if not user_data:
        raise HTTPException(status_code=401, detail="Invalid initData")

    return user_data


# ============================================
# PAGES
# ============================================

@router.get("/tenders", response_class=HTMLResponse)
async def webapp_tenders_page(request: Request):
    """Страница Mini App с тендерами."""
    return templates.TemplateResponse("webapp_tenders.html", {"request": request})


# ============================================
# API ENDPOINTS
# ============================================

@router.get("/api/tenders")
async def api_get_tenders(request: Request):
    """Получить тендеры пользователя за последние 7 дней."""
    user_data = await get_webapp_user(request)
    telegram_id = user_data.get('id')

    if not telegram_id:
        raise HTTPException(status_code=400, detail="No telegram_id")

    try:
        async with DatabaseSession() as session:
            # Находим пользователя
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == telegram_id)
            )
            if not user:
                return JSONResponse({"tenders": [], "total": 0})

            # Получаем тендеры за последние 7 дней
            since = datetime.utcnow() - timedelta(days=7)
            notifications = await session.scalars(
                select(SniperNotification)
                .where(and_(
                    SniperNotification.user_id == user.id,
                    SniperNotification.sent_at >= since
                ))
                .order_by(SniperNotification.sent_at.desc())
            )
            notifications = notifications.all()

            tenders = []
            for n in notifications:
                tenders.append({
                    "id": n.id,
                    "name": n.tender_name,
                    "number": n.tender_number,
                    "price": n.tender_price,
                    "score": n.score,
                    "deadline": n.submission_deadline.strftime('%d.%m.%Y') if n.submission_deadline else None,
                    "region": n.tender_region or '',
                    "customer": n.tender_customer or '',
                    "filter_name": n.filter_name or '',
                    "published_date": n.published_date.strftime('%d.%m.%Y') if n.published_date else None,
                    "sheets_exported": n.sheets_exported,
                    "url": n.tender_url or '',
                    "sent_at": n.sent_at.isoformat() if n.sent_at else None,
                })

            return JSONResponse({"tenders": tenders, "total": len(tenders)})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API get_tenders error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.post("/api/export")
async def api_export_tenders(request: Request):
    """Экспортировать выбранные тендеры в Google Sheets."""
    user_data = await get_webapp_user(request)
    telegram_id = user_data.get('id')

    if not telegram_id:
        raise HTTPException(status_code=400, detail="No telegram_id")

    body = await request.json()
    tender_ids = body.get('tender_ids', [])

    if not tender_ids:
        raise HTTPException(status_code=400, detail="No tender_ids provided")

    if len(tender_ids) > 50:
        raise HTTPException(status_code=400, detail="Too many tenders (max 50)")

    try:
        async with DatabaseSession() as session:
            # Находим пользователя
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == telegram_id)
            )
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            # Получаем Google Sheets config
            gs_config = await session.scalar(
                select(GoogleSheetsConfig).where(GoogleSheetsConfig.user_id == user.id)
            )
            if not gs_config or not gs_config.enabled:
                return JSONResponse({
                    "success": [],
                    "failed": tender_ids,
                    "error": "Google Sheets не настроен. Настройте через бота."
                }, status_code=400)

            # Получаем тендеры
            notifications = await session.scalars(
                select(SniperNotification).where(and_(
                    SniperNotification.id.in_(tender_ids),
                    SniperNotification.user_id == user.id
                ))
            )
            notifications = notifications.all()

            if not notifications:
                raise HTTPException(status_code=404, detail="No tenders found")

            # Экспортируем
            from tender_sniper.google_sheets_sync import get_sheets_sync, AI_COLUMNS, enrich_tender_with_ai
            sheets_sync = get_sheets_sync()

            if not sheets_sync:
                return JSONResponse({
                    "success": [],
                    "failed": tender_ids,
                    "error": "Google Sheets сервис недоступен"
                }, status_code=503)

            success_ids = []
            failed_ids = []
            errors = {}

            is_premium = user.subscription_tier == 'premium'
            user_columns = set(gs_config.columns or [])
            has_ai_columns = bool(user_columns & AI_COLUMNS)

            for notif in notifications:
                try:
                    tender_data = {
                        'number': notif.tender_number,
                        'name': notif.tender_name,
                        'price': notif.tender_price,
                        'url': notif.tender_url or '',
                        'region': notif.tender_region or '',
                        'customer_name': notif.tender_customer or '',
                        'published_date': notif.published_date.strftime('%d.%m.%Y') if notif.published_date else '',
                        'submission_deadline': notif.submission_deadline.strftime('%d.%m.%Y') if notif.submission_deadline else '',
                    }

                    # AI enrichment for Premium
                    ai_data = {}
                    if has_ai_columns and is_premium and gs_config.ai_enrichment:
                        try:
                            ai_data = await enrich_tender_with_ai(
                                tender_number=notif.tender_number,
                                tender_price=notif.tender_price,
                                customer_name=notif.tender_customer or '',
                                subscription_tier='premium'
                            )
                        except Exception as ai_err:
                            logger.warning(f"AI enrichment error for {notif.id}: {ai_err}")

                    match_data = {
                        'score': notif.score,
                        'red_flags': [],
                        'filter_name': notif.filter_name or '',
                        'ai_data': ai_data,
                    }

                    from tender_sniper.google_sheets_sync import get_weekly_sheet_name
                    await sheets_sync.append_tender(
                        spreadsheet_id=gs_config.spreadsheet_id,
                        tender_data=tender_data,
                        match_data=match_data,
                        columns=gs_config.columns or [],
                        sheet_name=get_weekly_sheet_name()
                    )

                    # Mark as exported
                    notif.sheets_exported = True
                    notif.sheets_exported_at = datetime.utcnow()
                    success_ids.append(notif.id)

                except Exception as exp_err:
                    logger.warning(f"Export error for notif {notif.id}: {exp_err}")
                    failed_ids.append(notif.id)
                    errors[str(notif.id)] = str(exp_err)

            return JSONResponse({
                "success": success_ids,
                "failed": failed_ids,
                "errors": errors
            })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API export error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")


@router.get("/api/user-info")
async def api_user_info(request: Request):
    """Информация о пользователе."""
    user_data = await get_webapp_user(request)
    telegram_id = user_data.get('id')

    if not telegram_id:
        raise HTTPException(status_code=400, detail="No telegram_id")

    try:
        async with DatabaseSession() as session:
            user = await session.scalar(
                select(SniperUser).where(SniperUser.telegram_id == telegram_id)
            )
            if not user:
                return JSONResponse({"error": "User not found"}, status_code=404)

            gs_config = await session.scalar(
                select(GoogleSheetsConfig).where(GoogleSheetsConfig.user_id == user.id)
            )

            return JSONResponse({
                "subscription_tier": user.subscription_tier,
                "has_google_sheets": gs_config is not None and gs_config.enabled,
                "sheets_email": None,  # Could be populated from service account
            })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API user-info error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal error")
