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

# Добавляем корень проекта в путь
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from fastapi import FastAPI, Request, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
    DatabaseSession,
)

logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "tender2024")

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
                "tier_free": tier_stats.get('free', 0),
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
    valid_tiers = ['free', 'basic', 'premium']
    if tier not in valid_tiers:
        raise HTTPException(status_code=400, detail="Неверный тариф")

    try:
        limits_map = {
            'free': {'filters': 5, 'notifications': 15},
            'basic': {'filters': 15, 'notifications': 50},
            'premium': {'filters': 9999, 'notifications': 9999}
        }

        async with DatabaseSession() as session:
            await session.execute(
                update(SniperUser)
                .where(SniperUser.id == user_id)
                .values(
                    subscription_tier=tier,
                    filters_limit=limits_map[tier]['filters'],
                    notifications_limit=limits_map[tier]['notifications']
                )
            )

        return RedirectResponse(url="/users", status_code=303)

    except Exception as e:
        logger.error(f"Set tier error: {e}", exc_info=True)
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
