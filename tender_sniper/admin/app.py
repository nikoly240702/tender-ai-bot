"""
FastAPI Admin Dashboard for Tender Sniper.

Веб-панель администратора с аналитикой, управлением пользователями и мониторингом.

Запуск: uvicorn tender_sniper.admin.app:app --host 0.0.0.0 --port 8080
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import logging
import asyncio
import aiohttp
import csv
import io
import json

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, Request, Depends, HTTPException, Form, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

from sqlalchemy import select, func, and_, distinct, update, delete, desc
from sqlalchemy.orm import selectinload

from database import (
    SniperUser,
    SniperFilter,
    SniperNotification,
    BroadcastMessage,
    Promocode,
    Payment,
    Referral,
    UserEvent,
    DatabaseSession,
)

logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "tender2024")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ============================================
# FASTAPI APP
# ============================================

app = FastAPI(
    title="Tender Sniper Admin",
    description="Панель администратора Tender Sniper",
    version="1.0.0"
)

# Static files and templates
BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# Security
security = HTTPBasic()


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Проверка базовой авторизации."""
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)

    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Неверные учетные данные",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


# ============================================
# DASHBOARD
# ============================================

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, username: str = Depends(verify_credentials)):
    """Главная страница - дашборд."""
    try:
        async with DatabaseSession() as session:
            # Общая статистика
            total_users = await session.scalar(select(func.count(SniperUser.id))) or 0
            active_filters = await session.scalar(
                select(func.count(SniperFilter.id)).where(SniperFilter.is_active == True)
            ) or 0
            total_filters = await session.scalar(select(func.count(SniperFilter.id))) or 0
            total_notifications = await session.scalar(select(func.count(SniperNotification.id))) or 0

            # За сегодня
            today = datetime.now().date()
            today_notifications = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    func.date(SniperNotification.sent_at) == today
                )
            ) or 0

            # За неделю
            week_ago = datetime.now() - timedelta(days=7)
            week_notifications = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    SniperNotification.sent_at >= week_ago
                )
            ) or 0

            # Новые пользователи за неделю
            new_users_week = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    SniperUser.created_at >= week_ago
                )
            ) or 0

            # Статистика по тарифам
            tier_stats_query = (
                select(
                    SniperUser.subscription_tier,
                    func.count(SniperUser.id).label('count')
                )
                .group_by(SniperUser.subscription_tier)
            )
            tier_result = await session.execute(tier_stats_query)
            tier_stats = {row[0]: row[1] for row in tier_result.all()}

            # Последние уведомления
            recent_query = (
                select(
                    SniperNotification.sent_at,
                    SniperNotification.tender_number,
                    SniperUser.telegram_id,
                    SniperFilter.name.label('filter_name')
                )
                .join(SniperUser, SniperNotification.user_id == SniperUser.id)
                .join(SniperFilter, SniperNotification.filter_id == SniperFilter.id)
                .order_by(SniperNotification.sent_at.desc())
                .limit(10)
            )
            recent_result = await session.execute(recent_query)
            recent_notifications = recent_result.all()

            # Активные пользователи (последние 24 часа)
            yesterday = datetime.now() - timedelta(hours=24)
            active_users = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    SniperUser.last_activity >= yesterday
                )
            ) or 0

            # Последние пользователи
            recent_users_query = (
                select(SniperUser)
                .order_by(SniperUser.created_at.desc())
                .limit(5)
            )
            recent_users_result = await session.execute(recent_users_query)
            recent_users = recent_users_result.scalars().all()

            # Последние фильтры
            recent_filters_query = (
                select(SniperFilter)
                .order_by(SniperFilter.created_at.desc())
                .limit(5)
            )
            recent_filters_result = await session.execute(recent_filters_query)
            recent_filters = recent_filters_result.scalars().all()

            current_time = datetime.now().strftime('%d.%m.%Y %H:%M')

        return templates.TemplateResponse("dashboard.html", {
            "request": request,
            "username": username,
            "current_time": current_time,
            "stats": {
                "total_users": total_users,
                "active_users": active_users,
                "total_filters": total_filters,
                "notifications_today": today_notifications,
                "tier_trial": tier_stats.get('trial', 0),
                "tier_basic": tier_stats.get('basic', 0),
                "tier_premium": tier_stats.get('premium', 0),
            },
            "recent_users": recent_users,
            "recent_filters": recent_filters,
        })

    except Exception as e:
        logger.error(f"Dashboard error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# USERS
# ============================================

@app.get("/users", response_class=HTMLResponse)
async def users_list(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
    tier: str = Query(""),
    username: str = Depends(verify_credentials)
):
    """Список пользователей."""
    try:
        per_page = 20
        offset = (page - 1) * per_page

        async with DatabaseSession() as session:
            # Базовый запрос
            query = select(SniperUser)
            count_query = select(func.count(SniperUser.id))

            # Фильтры
            if search:
                search_filter = SniperUser.telegram_id.cast(str).contains(search)
                query = query.where(search_filter)
                count_query = count_query.where(search_filter)

            if tier:
                query = query.where(SniperUser.subscription_tier == tier)
                count_query = count_query.where(SniperUser.subscription_tier == tier)

            # Подсчет
            total = await session.scalar(count_query) or 0
            total_pages = (total + per_page - 1) // per_page

            # Получаем пользователей
            query = query.order_by(SniperUser.created_at.desc()).offset(offset).limit(per_page)
            result = await session.execute(query)
            users = result.scalars().all()

            # Получаем статистику для каждого пользователя
            users_data = []
            for user in users:
                filters_count = await session.scalar(
                    select(func.count(SniperFilter.id)).where(SniperFilter.user_id == user.id)
                ) or 0
                active_filters = await session.scalar(
                    select(func.count(SniperFilter.id)).where(
                        and_(SniperFilter.user_id == user.id, SniperFilter.is_active == True)
                    )
                ) or 0
                notifications_count = await session.scalar(
                    select(func.count(SniperNotification.id)).where(
                        SniperNotification.user_id == user.id
                    )
                ) or 0

                users_data.append({
                    "user": user,
                    "filters_count": filters_count,
                    "active_filters": active_filters,
                    "notifications_count": notifications_count,
                })

            # Статистика по тарифам
            tier_stats_query = (
                select(
                    SniperUser.subscription_tier,
                    func.count(SniperUser.id).label('count')
                )
                .group_by(SniperUser.subscription_tier)
            )
            tier_result = await session.execute(tier_stats_query)
            tier_counts = {row[0]: row[1] for row in tier_result.all()}

        return templates.TemplateResponse("users.html", {
            "request": request,
            "username": username,
            "users": users_data,
            "page": page,
            "total_pages": total_pages,
            "total_users": total,
            "tier_counts": tier_counts,
            "search": search,
            "tier_filter": tier,
        })

    except Exception as e:
        logger.error(f"Users list error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


@app.post("/users/{user_id}/set-tier")
async def set_user_tier(
    user_id: int,
    tier: str = Form(...),
    username: str = Depends(verify_credentials)
):
    """Изменить тариф пользователя."""
    valid_tiers = ['trial', 'basic', 'premium']
    if tier not in valid_tiers:
        raise HTTPException(status_code=400, detail="Неверный тариф")

    try:
        limits_map = {
            'trial': {'filters': 3, 'notifications': 20, 'days': 7},
            'basic': {'filters': 5, 'notifications': 50, 'days': 30},
            'premium': {'filters': 20, 'notifications': 9999, 'days': 30}
        }

        async with DatabaseSession() as session:
            # Получаем текущего пользователя
            user = await session.get(SniperUser, user_id)
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")

            new_limits = limits_map[tier]
            now = datetime.now()

            # Вычисляем дату окончания подписки
            if tier in ['trial', 'basic', 'premium']:
                if user.trial_expires_at and user.trial_expires_at > now:
                    # Если есть активная подписка - добавляем дни к ней
                    new_expires = user.trial_expires_at + timedelta(days=new_limits['days'])
                else:
                    # Нет активной подписки - от сегодня
                    new_expires = now + timedelta(days=new_limits['days'])
            else:
                new_expires = None

            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == user_id)
                .values(
                    subscription_tier=tier,
                    filters_limit=new_limits['filters'],
                    notifications_limit=new_limits['notifications'],
                    trial_expires_at=new_expires
                )
            )

        return RedirectResponse(url="/users", status_code=303)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Set tier error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/users/{user_id}/add-days")
async def add_subscription_days(
    user_id: int,
    days: int = Form(...),
    tier: str = Form("premium"),
    username: str = Depends(verify_credentials)
):
    """Добавить дни к подписке пользователя."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Количество дней должно быть от 1 до 365")

    valid_tiers = ['trial', 'basic', 'premium']
    if tier not in valid_tiers:
        tier = 'premium'

    try:
        limits_map = {
            'trial': {'filters': 3, 'notifications': 20},
            'basic': {'filters': 5, 'notifications': 50},
            'premium': {'filters': 20, 'notifications': 9999}
        }

        async with DatabaseSession() as session:
            user = await session.get(SniperUser, user_id)
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")

            now = datetime.now()
            new_limits = limits_map[tier]

            # Если есть активная подписка - добавляем к ней, иначе от сегодня
            if user.trial_expires_at and user.trial_expires_at > now:
                new_expires = user.trial_expires_at + timedelta(days=days)
            else:
                new_expires = now + timedelta(days=days)

            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == user_id)
                .values(
                    subscription_tier=tier,
                    filters_limit=new_limits['filters'],
                    notifications_limit=new_limits['notifications'],
                    trial_expires_at=new_expires
                )
            )

        return RedirectResponse(url="/users", status_code=303)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add days error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/users/{user_id}/block")
async def block_user(
    user_id: int,
    reason: str = Form(""),
    username: str = Depends(verify_credentials)
):
    """Заблокировать пользователя."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == user_id)
                .values(
                    status='blocked',
                    blocked_reason=reason or "Заблокирован администратором",
                    blocked_at=datetime.now()
                )
            )

        return RedirectResponse(url="/users", status_code=303)

    except Exception as e:
        logger.error(f"Block user error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/users/{user_id}/unblock")
async def unblock_user(
    user_id: int,
    username: str = Depends(verify_credentials)
):
    """Разблокировать пользователя."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == user_id)
                .values(
                    status='active',
                    blocked_reason=None,
                    blocked_at=None
                )
            )

        return RedirectResponse(url="/users", status_code=303)

    except Exception as e:
        logger.error(f"Unblock user error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# FILTERS
# ============================================

@app.get("/filters", response_class=HTMLResponse)
async def filters_list(
    request: Request,
    page: int = Query(1, ge=1),
    search: str = Query(""),
    active_only: bool = Query(False),
    username: str = Depends(verify_credentials)
):
    """Список фильтров."""
    try:
        per_page = 20
        offset = (page - 1) * per_page

        async with DatabaseSession() as session:
            # Базовый запрос
            query = (
                select(SniperFilter, SniperUser.telegram_id.label('user_telegram_id'))
                .join(SniperUser, SniperFilter.user_id == SniperUser.id)
            )
            count_query = select(func.count(SniperFilter.id))

            # Фильтры
            if search:
                search_filter = SniperFilter.name.contains(search)
                query = query.where(search_filter)
                count_query = count_query.where(search_filter)

            if active_only:
                query = query.where(SniperFilter.is_active == True)
                count_query = count_query.where(SniperFilter.is_active == True)

            # Подсчет
            total = await session.scalar(count_query) or 0
            total_pages = (total + per_page - 1) // per_page

            # Получаем фильтры
            query = query.order_by(SniperFilter.created_at.desc()).offset(offset).limit(per_page)
            result = await session.execute(query)
            filters_raw = result.all()

            # Получаем статистику для каждого фильтра
            filters_data = []
            for row in filters_raw:
                filter_obj = row[0]
                user_telegram_id = row[1]

                notifications_count = await session.scalar(
                    select(func.count(SniperNotification.id)).where(
                        SniperNotification.filter_id == filter_obj.id
                    )
                ) or 0

                filters_data.append({
                    "filter": filter_obj,
                    "user_telegram_id": user_telegram_id,
                    "notifications_count": notifications_count,
                })

            # Подсчет активных фильтров
            active_count = await session.scalar(
                select(func.count(SniperFilter.id)).where(SniperFilter.is_active == True)
            ) or 0

        return templates.TemplateResponse("filters.html", {
            "request": request,
            "username": username,
            "filters": filters_data,
            "page": page,
            "total_pages": total_pages,
            "total_filters": total,
            "active_count": active_count,
            "search": search,
            "status_filter": "active" if active_only else "",
        })

    except Exception as e:
        logger.error(f"Filters list error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


@app.post("/filters/{filter_id}/toggle")
async def toggle_filter(
    filter_id: int,
    username: str = Depends(verify_credentials)
):
    """Включить/выключить фильтр."""
    try:
        async with DatabaseSession() as session:
            # Получаем текущий статус
            filter_obj = await session.get(SniperFilter, filter_id)
            if not filter_obj:
                raise HTTPException(status_code=404, detail="Фильтр не найден")

            # Переключаем
            await session.execute(
                update(SniperFilter)
                .where(SniperFilter.id == filter_id)
                .values(is_active=not filter_obj.is_active)
            )

        return RedirectResponse(url="/filters", status_code=303)

    except Exception as e:
        logger.error(f"Toggle filter error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/filters/{filter_id}/delete")
async def delete_filter(
    filter_id: int,
    username: str = Depends(verify_credentials)
):
    """Удалить фильтр."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                delete(SniperFilter).where(SniperFilter.id == filter_id)
            )

        return RedirectResponse(url="/filters", status_code=303)

    except Exception as e:
        logger.error(f"Delete filter error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# NOTIFICATIONS
# ============================================

@app.get("/notifications", response_class=HTMLResponse)
async def notifications_list(
    request: Request,
    page: int = Query(1, ge=1),
    username: str = Depends(verify_credentials)
):
    """Список уведомлений."""
    try:
        per_page = 50
        offset = (page - 1) * per_page

        async with DatabaseSession() as session:
            # Подсчет
            total = await session.scalar(select(func.count(SniperNotification.id))) or 0
            total_pages = (total + per_page - 1) // per_page

            # Получаем уведомления
            query = (
                select(
                    SniperNotification,
                    SniperUser.telegram_id.label('user_telegram_id'),
                    SniperFilter.name.label('filter_name')
                )
                .join(SniperUser, SniperNotification.user_id == SniperUser.id)
                .join(SniperFilter, SniperNotification.filter_id == SniperFilter.id)
                .order_by(SniperNotification.sent_at.desc())
                .offset(offset)
                .limit(per_page)
            )
            result = await session.execute(query)
            notifications = result.all()

        return templates.TemplateResponse("notifications.html", {
            "request": request,
            "username": username,
            "notifications": notifications,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        })

    except Exception as e:
        logger.error(f"Notifications list error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# ANALYTICS API
# ============================================

@app.get("/api/stats/hourly")
async def hourly_stats(
    hours: int = Query(24, ge=1, le=168),
    username: str = Depends(verify_credentials)
):
    """Статистика уведомлений по часам."""
    try:
        async with DatabaseSession() as session:
            since = datetime.now() - timedelta(hours=hours)

            query = (
                select(SniperNotification.sent_at)
                .where(SniperNotification.sent_at >= since)
                .order_by(SniperNotification.sent_at)
            )
            result = await session.execute(query)
            notifications = result.scalars().all()

            # Группируем по часам
            from collections import defaultdict
            hourly = defaultdict(int)
            for sent_at in notifications:
                hour_key = sent_at.strftime('%Y-%m-%d %H:00')
                hourly[hour_key] += 1

            return JSONResponse({
                "labels": list(hourly.keys()),
                "data": list(hourly.values())
            })

    except Exception as e:
        logger.error(f"Hourly stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats/daily")
async def daily_stats(
    days: int = Query(30, ge=1, le=90),
    username: str = Depends(verify_credentials)
):
    """Статистика уведомлений по дням."""
    try:
        async with DatabaseSession() as session:
            since = datetime.now() - timedelta(days=days)

            query = (
                select(SniperNotification.sent_at)
                .where(SniperNotification.sent_at >= since)
            )
            result = await session.execute(query)
            notifications = result.scalars().all()

            # Группируем по дням
            from collections import defaultdict
            daily = defaultdict(int)
            for sent_at in notifications:
                day_key = sent_at.strftime('%Y-%m-%d')
                daily[day_key] += 1

            return JSONResponse({
                "labels": list(daily.keys()),
                "data": list(daily.values())
            })

    except Exception as e:
        logger.error(f"Daily stats error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# HEALTH CHECK
# ============================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.now().isoformat()}


# ============================================
# FORCE REFRESH BOT (Обновить бота для всех)
# ============================================

async def send_refresh_message(telegram_id: int, message_text: str) -> bool:
    """Отправить сообщение с кнопкой перезапуска бота."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": telegram_id,
        "text": message_text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [{"text": "🔄 Перезапустить бота", "callback_data": "force_restart"}]
            ]
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                return resp.status == 200
    except Exception as e:
        logger.error(f"Failed to send refresh message to {telegram_id}: {e}")
        return False


async def force_refresh_task(user_ids: list, message_text: str):
    """Фоновая задача для отправки сообщений о перезапуске."""
    successful = 0
    failed = 0

    for telegram_id in user_ids:
        if await send_refresh_message(telegram_id, message_text):
            successful += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)

    logger.info(f"Force refresh completed: {successful} success, {failed} failed")
    return successful, failed


@app.post("/force-refresh")
async def force_refresh_all_users(
    background_tasks: BackgroundTasks,
    message: str = Form(""),
    username: str = Depends(verify_credentials)
):
    """
    Отправить всем пользователям сообщение с кнопкой перезапуска бота.
    Это обновит клавиатуры у всех пользователей.
    """
    try:
        default_message = (
            "🔄 <b>Доступно обновление бота!</b>\n\n"
            "Нажмите кнопку ниже, чтобы получить последнюю версию с новыми функциями и исправлениями."
        )
        final_message = message.strip() if message.strip() else default_message

        async with DatabaseSession() as session:
            query = select(SniperUser.telegram_id).where(SniperUser.status == 'active')
            result = await session.execute(query)
            user_ids = [row[0] for row in result.all()]

        if user_ids:
            background_tasks.add_task(force_refresh_task, user_ids, final_message)

        return RedirectResponse(url=f"/broadcast?refresh_sent={len(user_ids)}", status_code=303)

    except Exception as e:
        logger.error(f"Force refresh error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# BROADCAST (Рассылка)
# ============================================

async def send_telegram_message(telegram_id: int, text: str) -> bool:
    """Отправить сообщение через Telegram API."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": telegram_id,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=10) as resp:
                return resp.status == 200
    except Exception as e:
        logger.error(f"Failed to send message to {telegram_id}: {e}")
        return False


async def broadcast_task(broadcast_id: int, user_ids: list, message_text: str):
    """Фоновая задача для рассылки сообщений."""
    successful = 0
    failed = 0

    for telegram_id in user_ids:
        if await send_telegram_message(telegram_id, message_text):
            successful += 1
        else:
            failed += 1
        # Задержка между сообщениями (Telegram rate limit)
        await asyncio.sleep(0.05)  # 20 сообщений в секунду

    # Обновляем статистику рассылки
    async with DatabaseSession() as session:
        await session.execute(
            update(BroadcastMessage)
            .where(BroadcastMessage.id == broadcast_id)
            .values(successful=successful, failed=failed)
        )

    logger.info(f"Broadcast {broadcast_id} completed: {successful} success, {failed} failed")


@app.get("/broadcast", response_class=HTMLResponse)
async def broadcast_page(
    request: Request,
    username: str = Depends(verify_credentials)
):
    """Страница рассылки сообщений."""
    try:
        async with DatabaseSession() as session:
            # Статистика по тарифам
            tier_stats = {}
            for tier in ['all', 'trial', 'basic', 'premium']:
                if tier == 'all':
                    count = await session.scalar(select(func.count(SniperUser.id))) or 0
                else:
                    count = await session.scalar(
                        select(func.count(SniperUser.id)).where(SniperUser.subscription_tier == tier)
                    ) or 0
                tier_stats[tier] = count

            # История рассылок
            history_query = (
                select(BroadcastMessage)
                .order_by(BroadcastMessage.sent_at.desc())
                .limit(20)
            )
            history_result = await session.execute(history_query)
            history = history_result.scalars().all()

        return templates.TemplateResponse("broadcast.html", {
            "request": request,
            "username": username,
            "tier_stats": tier_stats,
            "history": history,
        })

    except Exception as e:
        logger.error(f"Broadcast page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


@app.post("/broadcast/send")
async def send_broadcast(
    background_tasks: BackgroundTasks,
    message: str = Form(...),
    target_tier: str = Form("all"),
    username: str = Depends(verify_credentials)
):
    """Отправить рассылку."""
    valid_tiers = ['all', 'trial', 'basic', 'premium']
    if target_tier not in valid_tiers:
        raise HTTPException(status_code=400, detail="Неверный тариф")

    if not message.strip():
        raise HTTPException(status_code=400, detail="Сообщение не может быть пустым")

    try:
        async with DatabaseSession() as session:
            # Получаем пользователей
            if target_tier == 'all':
                query = select(SniperUser.telegram_id).where(SniperUser.status == 'active')
            else:
                query = select(SniperUser.telegram_id).where(
                    and_(
                        SniperUser.status == 'active',
                        SniperUser.subscription_tier == target_tier
                    )
                )

            result = await session.execute(query)
            user_ids = [row[0] for row in result.all()]

            # Создаём запись о рассылке
            broadcast = BroadcastMessage(
                message_text=message,
                target_tier=target_tier,
                total_recipients=len(user_ids),
                successful=0,
                failed=0,
                created_by=username
            )
            session.add(broadcast)
            await session.flush()
            broadcast_id = broadcast.id

        # Запускаем рассылку в фоне
        if user_ids:
            background_tasks.add_task(broadcast_task, broadcast_id, user_ids, message)

        return RedirectResponse(url="/broadcast?sent=1", status_code=303)

    except Exception as e:
        logger.error(f"Send broadcast error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/broadcast/history", response_class=HTMLResponse)
async def broadcast_history(
    request: Request,
    page: int = Query(1, ge=1),
    username: str = Depends(verify_credentials)
):
    """История рассылок."""
    try:
        per_page = 20
        offset = (page - 1) * per_page

        async with DatabaseSession() as session:
            total = await session.scalar(select(func.count(BroadcastMessage.id))) or 0
            total_pages = (total + per_page - 1) // per_page

            query = (
                select(BroadcastMessage)
                .order_by(BroadcastMessage.sent_at.desc())
                .offset(offset)
                .limit(per_page)
            )
            result = await session.execute(query)
            broadcasts = result.scalars().all()

        return templates.TemplateResponse("broadcast_history.html", {
            "request": request,
            "username": username,
            "broadcasts": broadcasts,
            "page": page,
            "total_pages": total_pages,
            "total": total,
        })

    except Exception as e:
        logger.error(f"Broadcast history error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# ANALYTICS (Аналитика)
# ============================================

@app.get("/analytics", response_class=HTMLResponse)
async def analytics_page(
    request: Request,
    username: str = Depends(verify_credentials)
):
    """Страница аналитики."""
    try:
        async with DatabaseSession() as session:
            # Топ ключевых слов
            keywords_query = select(SniperFilter.keywords).where(SniperFilter.keywords.isnot(None))
            keywords_result = await session.execute(keywords_query)

            keyword_counts = {}
            for row in keywords_result.all():
                keywords_data = row[0]
                if keywords_data:
                    # Поддержка и списка, и строки
                    if isinstance(keywords_data, list):
                        keywords_list = keywords_data
                    else:
                        keywords_list = [kw.strip() for kw in str(keywords_data).split(',')]

                    for kw in keywords_list:
                        kw = str(kw).strip().lower()
                        if kw:
                            keyword_counts[kw] = keyword_counts.get(kw, 0) + 1

            top_keywords = sorted(keyword_counts.items(), key=lambda x: -x[1])[:20]

            # Топ регионов
            regions_query = select(SniperFilter.regions).where(SniperFilter.regions.isnot(None))
            regions_result = await session.execute(regions_query)

            region_counts = {}
            for row in regions_result.all():
                regions_data = row[0]
                if regions_data:
                    # Поддержка и списка, и строки
                    if isinstance(regions_data, list):
                        regions_list = regions_data
                    else:
                        regions_list = [r.strip() for r in str(regions_data).split(',')]

                    for region in regions_list:
                        region = str(region).strip()
                        if region:
                            region_counts[region] = region_counts.get(region, 0) + 1

            top_regions = sorted(region_counts.items(), key=lambda x: -x[1])[:15]

            # Воронка конверсии
            total_users = await session.scalar(select(func.count(SniperUser.id))) or 0

            users_with_filters = await session.scalar(
                select(func.count(distinct(SniperFilter.user_id)))
            ) or 0

            yesterday = datetime.now() - timedelta(hours=24)
            active_24h = await session.scalar(
                select(func.count(SniperUser.id)).where(SniperUser.last_activity >= yesterday)
            ) or 0

            paying_users = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    SniperUser.subscription_tier.in_(['basic', 'premium'])
                )
            ) or 0

            funnel = {
                'registered': total_users,
                'created_filter': users_with_filters,
                'active_24h': active_24h,
                'paying': paying_users,
            }

            # Статистика по тарифам
            tier_stats_query = (
                select(
                    SniperUser.subscription_tier,
                    func.count(SniperUser.id).label('count')
                )
                .group_by(SniperUser.subscription_tier)
            )
            tier_result = await session.execute(tier_stats_query)
            tier_stats = {row[0]: row[1] for row in tier_result.all()}

            # Регистрации по дням (последние 14 дней)
            fourteen_days_ago = datetime.utcnow() - timedelta(days=14)
            registrations_query = (
                select(
                    func.date(SniperUser.created_at).label('date'),
                    func.count(SniperUser.id).label('count')
                )
                .where(SniperUser.created_at >= fourteen_days_ago)
                .group_by(func.date(SniperUser.created_at))
                .order_by(func.date(SniperUser.created_at))
            )
            reg_result = await session.execute(registrations_query)
            daily_registrations = [
                {'date': str(row[0]), 'count': row[1]}
                for row in reg_result.all()
            ]

            # Заблокировали бота (status != 'active')
            blocked_users = await session.scalar(
                select(func.count(SniperUser.id)).where(SniperUser.status == 'blocked')
            ) or 0

            # Неактивные 7+ дней
            week_ago = datetime.utcnow() - timedelta(days=7)
            inactive_users = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(
                        SniperUser.status == 'active',
                        SniperUser.last_activity < week_ago
                    )
                )
            ) or 0

            # События (если таблица существует)
            try:
                events_today = await session.scalar(
                    select(func.count(UserEvent.id)).where(
                        UserEvent.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)
                    )
                ) or 0
            except:
                events_today = 0

            churn_stats = {
                'blocked': blocked_users,
                'inactive_7d': inactive_users,
                'events_today': events_today,
            }

        return templates.TemplateResponse("analytics.html", {
            "request": request,
            "username": username,
            "top_keywords": top_keywords,
            "top_regions": top_regions,
            "funnel": funnel,
            "tier_stats": tier_stats,
            "daily_registrations": daily_registrations,
            "churn_stats": churn_stats,
        })

    except Exception as e:
        logger.error(f"Analytics page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# PROMOCODES (Промокоды)
# ============================================

@app.get("/promocodes", response_class=HTMLResponse)
async def promocodes_page(
    request: Request,
    username: str = Depends(verify_credentials)
):
    """Страница промокодов."""
    try:
        async with DatabaseSession() as session:
            query = (
                select(Promocode)
                .order_by(Promocode.created_at.desc())
            )
            result = await session.execute(query)
            promocodes = result.scalars().all()

        return templates.TemplateResponse("promocodes.html", {
            "request": request,
            "username": username,
            "promocodes": promocodes,
        })

    except Exception as e:
        logger.error(f"Promocodes page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


@app.post("/promocodes/create")
async def create_promocode(
    code: str = Form(...),
    tier: str = Form(...),
    days: int = Form(...),
    max_uses: int = Form(None),
    expires_at: str = Form(None),
    username: str = Depends(verify_credentials)
):
    """Создать промокод."""
    valid_tiers = ['basic', 'premium']
    if tier not in valid_tiers:
        raise HTTPException(status_code=400, detail="Неверный тариф")

    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Дней должно быть от 1 до 365")

    try:
        expires = None
        if expires_at:
            expires = datetime.strptime(expires_at, '%Y-%m-%d')

        async with DatabaseSession() as session:
            # Проверяем уникальность
            existing = await session.scalar(
                select(Promocode.id).where(Promocode.code == code.upper())
            )
            if existing:
                raise HTTPException(status_code=400, detail="Промокод уже существует")

            promocode = Promocode(
                code=code.upper(),
                tier=tier,
                days=days,
                max_uses=max_uses,
                expires_at=expires,
                created_by=username
            )
            session.add(promocode)

        return RedirectResponse(url="/promocodes?created=1", status_code=303)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Create promocode error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/promocodes/{promocode_id}/deactivate")
async def deactivate_promocode(
    promocode_id: int,
    username: str = Depends(verify_credentials)
):
    """Деактивировать промокод."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                update(Promocode)
                .where(Promocode.id == promocode_id)
                .values(is_active=False)
            )

        return RedirectResponse(url="/promocodes", status_code=303)

    except Exception as e:
        logger.error(f"Deactivate promocode error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/promocodes/{promocode_id}/activate")
async def activate_promocode(
    promocode_id: int,
    username: str = Depends(verify_credentials)
):
    """Активировать промокод."""
    try:
        async with DatabaseSession() as session:
            await session.execute(
                update(Promocode)
                .where(Promocode.id == promocode_id)
                .values(is_active=True)
            )

        return RedirectResponse(url="/promocodes", status_code=303)

    except Exception as e:
        logger.error(f"Activate promocode error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# TARIFFS (Тарифы)
# ============================================

# Настройки тарифов (можно вынести в ENV или таблицу)
TARIFF_SETTINGS = {
    'free': {'filters': 3, 'notifications': 20, 'price': 0},
    'trial': {'filters': 3, 'notifications': 20, 'price': 0},
    'basic': {'filters': 5, 'notifications': 100, 'price': 490},
    'premium': {'filters': 20, 'notifications': 9999, 'price': 990},
}

@app.get("/tariffs", response_class=HTMLResponse)
async def tariffs_page(
    request: Request,
    username: str = Depends(verify_credentials)
):
    """Страница настройки тарифов."""
    try:
        async with DatabaseSession() as session:
            # Статистика по тарифам
            tier_stats = {}
            for tier in TARIFF_SETTINGS.keys():
                count = await session.scalar(
                    select(func.count(SniperUser.id)).where(SniperUser.subscription_tier == tier)
                ) or 0
                tier_stats[tier] = count

        return templates.TemplateResponse("tariffs.html", {
            "request": request,
            "username": username,
            "tariffs": TARIFF_SETTINGS,
            "tier_stats": tier_stats,
        })

    except Exception as e:
        logger.error(f"Tariffs page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# EXPORTS (Экспорт данных)
# ============================================

@app.get("/export/users")
async def export_users_csv(username: str = Depends(verify_credentials)):
    """Экспорт пользователей в CSV."""
    try:
        async with DatabaseSession() as session:
            query = select(SniperUser).order_by(SniperUser.created_at.desc())
            result = await session.execute(query)
            users = result.scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'Telegram ID', 'Username', 'First Name', 'Tier', 'Status', 'Created At', 'Last Activity'])

        for user in users:
            writer.writerow([
                user.id,
                user.telegram_id,
                user.username or '',
                user.first_name or '',
                user.subscription_tier,
                user.status,
                user.created_at.strftime('%Y-%m-%d %H:%M') if user.created_at else '',
                user.last_activity.strftime('%Y-%m-%d %H:%M') if user.last_activity else '',
            ])

        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=users_{datetime.now().strftime('%Y%m%d')}.csv"}
        )

    except Exception as e:
        logger.error(f"Export users error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/export/filters")
async def export_filters_csv(username: str = Depends(verify_credentials)):
    """Экспорт фильтров в CSV."""
    try:
        async with DatabaseSession() as session:
            query = (
                select(SniperFilter, SniperUser.telegram_id)
                .join(SniperUser, SniperFilter.user_id == SniperUser.id)
                .order_by(SniperFilter.created_at.desc())
            )
            result = await session.execute(query)
            filters_data = result.all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['ID', 'User Telegram ID', 'Name', 'Keywords', 'Regions', 'Active', 'Created At'])

        for filter_obj, telegram_id in filters_data:
            writer.writerow([
                filter_obj.id,
                telegram_id,
                filter_obj.name or '',
                filter_obj.keywords or '',
                filter_obj.regions or '',
                'Да' if filter_obj.is_active else 'Нет',
                filter_obj.created_at.strftime('%Y-%m-%d %H:%M') if filter_obj.created_at else '',
            ])

        output.seek(0)

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=filters_{datetime.now().strftime('%Y%m%d')}.csv"}
        )

    except Exception as e:
        logger.error(f"Export filters error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# PAYMENTS (Платежи)
# ============================================

@app.get("/payments", response_class=HTMLResponse)
async def payments_page(
    request: Request,
    page: int = Query(1, ge=1),
    status: str = Query(""),
    username: str = Depends(verify_credentials)
):
    """История платежей."""
    try:
        per_page = 30
        offset = (page - 1) * per_page

        async with DatabaseSession() as session:
            query = (
                select(Payment, SniperUser.telegram_id, SniperUser.username)
                .join(SniperUser, Payment.user_id == SniperUser.id)
            )
            count_query = select(func.count(Payment.id))

            if status:
                query = query.where(Payment.status == status)
                count_query = count_query.where(Payment.status == status)

            total = await session.scalar(count_query) or 0
            total_pages = (total + per_page - 1) // per_page

            query = query.order_by(Payment.created_at.desc()).offset(offset).limit(per_page)
            result = await session.execute(query)
            payments_data = result.all()

            # Общая сумма успешных платежей
            total_revenue = await session.scalar(
                select(func.sum(Payment.amount)).where(Payment.status == 'succeeded')
            ) or 0

        return templates.TemplateResponse("payments.html", {
            "request": request,
            "username": username,
            "payments": payments_data,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "total_revenue": total_revenue,
            "status_filter": status,
        })

    except Exception as e:
        logger.error(f"Payments page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# PRIVACY POLICY (без авторизации)
# ============================================

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page(request: Request):
    """Политика конфиденциальности (публичная страница)."""
    return templates.TemplateResponse("privacy.html", {
        "request": request,
    })


# ============================================
# YOOKASSA WEBHOOK (без авторизации)
# ============================================

@app.post("/payment/webhook")
async def yookassa_webhook(request: Request):
    """
    Webhook для обработки уведомлений от YooKassa.

    YooKassa отправляет POST запрос при изменении статуса платежа.
    """
    try:
        data = await request.json()
        event = data.get('event', '')

        logger.info(f"YooKassa webhook received: {event}")

        if event == 'payment.succeeded':
            obj = data.get('object', {})
            payment_id = obj.get('id')
            metadata = obj.get('metadata', {})
            amount = float(obj.get('amount', {}).get('value', 0))

            telegram_id = metadata.get('telegram_id')
            tier = metadata.get('tier')

            if not telegram_id or not tier:
                logger.warning(f"Missing metadata in webhook: {data}")
                return JSONResponse({'status': 'error', 'message': 'Missing metadata'})

            telegram_id = int(telegram_id)

            async with DatabaseSession() as session:
                # Находим пользователя
                user = await session.scalar(
                    select(SniperUser).where(SniperUser.telegram_id == telegram_id)
                )

                if not user:
                    logger.warning(f"User not found: {telegram_id}")
                    return JSONResponse({'status': 'error', 'message': 'User not found'})

                # Обновляем подписку
                from datetime import timedelta
                now = datetime.now()

                limits_map = {
                    'basic': {'filters': 5, 'notifications': 100},
                    'premium': {'filters': 20, 'notifications': 9999}
                }

                await session.execute(
                    update(SniperUser)
                    .where(SniperUser.id == user.id)
                    .values(
                        subscription_tier=tier,
                        filters_limit=limits_map.get(tier, {}).get('filters', 15),
                        notifications_limit=limits_map.get(tier, {}).get('notifications', 50)
                    )
                )

                # Записываем платёж
                payment_record = Payment(
                    user_id=user.id,
                    yookassa_payment_id=payment_id,
                    amount=amount,
                    tier=tier,
                    status='succeeded',
                    completed_at=now
                )
                session.add(payment_record)

            logger.info(f"Payment processed: user={telegram_id}, tier={tier}, amount={amount}")

            # Отправляем уведомление пользователю через Telegram
            if TELEGRAM_BOT_TOKEN:
                message = (
                    f"✅ <b>Оплата прошла успешно!</b>\n\n"
                    f"Тариф: <b>{tier.upper()}</b>\n"
                    f"Сумма: {amount:.0f} ₽\n\n"
                    f"Спасибо за подписку! Теперь вам доступны все возможности тарифа."
                )
                await send_telegram_message(telegram_id, message)

            return JSONResponse({'status': 'ok'})

        elif event == 'payment.canceled':
            obj = data.get('object', {})
            payment_id = obj.get('id')
            metadata = obj.get('metadata', {})
            telegram_id = metadata.get('telegram_id')

            if telegram_id:
                async with DatabaseSession() as session:
                    user = await session.scalar(
                        select(SniperUser).where(SniperUser.telegram_id == int(telegram_id))
                    )
                    if user:
                        # Записываем отменённый платёж
                        payment_record = Payment(
                            user_id=user.id,
                            yookassa_payment_id=payment_id,
                            amount=float(obj.get('amount', {}).get('value', 0)),
                            tier=metadata.get('tier', 'unknown'),
                            status='canceled'
                        )
                        session.add(payment_record)

            logger.info(f"Payment canceled: {payment_id}")
            return JSONResponse({'status': 'ok'})

        return JSONResponse({'status': 'ok', 'message': f'Event {event} ignored'})

    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return JSONResponse({'status': 'error', 'message': str(e)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
