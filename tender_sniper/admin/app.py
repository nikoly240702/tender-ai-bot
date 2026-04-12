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
    UserFeedback,
    TenderFavorite,
    HiddenTender,
    GptSession,
    GptMessage,
    DatabaseSession,
)

from tender_sniper.admin.metrika import MetrikaService

logger = logging.getLogger(__name__)

# ============================================
# CONFIGURATION
# ============================================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "tender2024")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# Metrika service
metrika_service = MetrikaService()

# Tariff prices for economics calculations
TARIFF_PRICES = {
    'starter': 499,
    'pro': 1490,
    'premium': 2990,
}

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
                "tier_basic": tier_stats.get('starter', 0) + tier_stats.get('basic', 0),
                "tier_starter": tier_stats.get('starter', 0) + tier_stats.get('basic', 0),
                "tier_pro": tier_stats.get('pro', 0),
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
    activity: str = Query(""),
    sort: str = Query("created_desc"),
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
                # Поиск по telegram_id (числовой) или по имени
                search_filter = SniperUser.telegram_id == int(search) if search.isdigit() else SniperUser.first_name.ilike(f"%{search}%")
                query = query.where(search_filter)
                count_query = count_query.where(search_filter)

            if tier:
                if tier == 'expired':
                    # Expired = trial с истекшей подпиской
                    now = datetime.now()
                    query = query.where(
                        and_(
                            SniperUser.subscription_tier == 'trial',
                            SniperUser.trial_expires_at != None,
                            SniperUser.trial_expires_at < now
                        )
                    )
                    count_query = count_query.where(
                        and_(
                            SniperUser.subscription_tier == 'trial',
                            SniperUser.trial_expires_at != None,
                            SniperUser.trial_expires_at < now
                        )
                    )
                else:
                    query = query.where(SniperUser.subscription_tier == tier)
                    count_query = count_query.where(SniperUser.subscription_tier == tier)

            # Activity filter
            if activity == 'active_7d':
                week_ago = datetime.now() - timedelta(days=7)
                query = query.where(SniperUser.last_activity >= week_ago)
                count_query = count_query.where(SniperUser.last_activity >= week_ago)
            elif activity == 'inactive':
                week_ago = datetime.now() - timedelta(days=7)
                query = query.where(
                    (SniperUser.last_activity == None) | (SniperUser.last_activity < week_ago)
                )
                count_query = count_query.where(
                    (SniperUser.last_activity == None) | (SniperUser.last_activity < week_ago)
                )

            # Подсчет
            total = await session.scalar(count_query) or 0
            total_pages = (total + per_page - 1) // per_page

            # Получаем пользователей с агрегированной статистикой одним запросом
            from sqlalchemy import case
            from sqlalchemy.orm import aliased

            # Подзапросы для агрегаций
            filters_sub = (
                select(
                    SniperFilter.user_id,
                    func.count(SniperFilter.id).label('total_filters'),
                    func.count(case((SniperFilter.is_active == True, 1))).label('active_filters')
                )
                .group_by(SniperFilter.user_id)
            ).subquery()

            notif_sub = (
                select(
                    SniperNotification.user_id,
                    func.count(SniperNotification.id).label('total_notifications')
                )
                .group_by(SniperNotification.user_id)
            ).subquery()

            # GPT messages subquery
            gpt_sub = (
                select(
                    GptSession.user_id,
                    func.count(GptMessage.id).label('gpt_messages')
                )
                .select_from(GptMessage)
                .join(GptSession, GptMessage.session_id == GptSession.id)
                .where(GptMessage.role == 'user')
                .group_by(GptSession.user_id)
            ).subquery()

            # Основной запрос с LEFT JOIN
            main_query = (
                query
                .outerjoin(filters_sub, SniperUser.id == filters_sub.c.user_id)
                .outerjoin(notif_sub, SniperUser.id == notif_sub.c.user_id)
                .outerjoin(gpt_sub, SniperUser.id == gpt_sub.c.user_id)
                .add_columns(
                    func.coalesce(filters_sub.c.total_filters, 0).label('filters_count'),
                    func.coalesce(filters_sub.c.active_filters, 0).label('active_filters_count'),
                    func.coalesce(notif_sub.c.total_notifications, 0).label('notifications_count'),
                    func.coalesce(gpt_sub.c.gpt_messages, 0).label('gpt_messages_count'),
                )
            )

            # Sorting
            if sort == 'activity_desc':
                main_query = main_query.order_by(SniperUser.last_activity.desc().nullslast())
            elif sort == 'notifications_desc':
                main_query = main_query.order_by(desc('notifications_count'))
            elif sort == 'created_asc':
                main_query = main_query.order_by(SniperUser.created_at.asc())
            else:  # created_desc (default)
                main_query = main_query.order_by(SniperUser.created_at.desc())

            main_query = main_query.offset(offset).limit(per_page)
            result = await session.execute(main_query)
            rows = result.all()

            # Формируем данные
            users_data = []
            now = datetime.now()
            for row in rows:
                user = row[0]
                filters_count = row[1]
                active_filters = row[2]
                notifications_count = row[3]
                gpt_messages_count = row[4]

                # Вычисляем оставшиеся дни подписки
                days_left = None
                expires_date = None
                if user.trial_expires_at:
                    expires_date = user.trial_expires_at.strftime('%d.%m')
                    delta = user.trial_expires_at - now
                    days_left = delta.days if delta.days >= 0 else -1  # -1 = истекла
                elif user.subscription_tier == 'trial':
                    # trial без даты окончания — считаем истекшим
                    days_left = -1

                # Status badge
                if user.status == 'blocked':
                    status_badge = 'blocked'
                elif days_left is not None and days_left < 0:
                    status_badge = 'expired'
                elif user.last_activity and (now - user.last_activity).days <= 7:
                    status_badge = 'active'
                else:
                    status_badge = 'inactive'

                users_data.append({
                    "user": user,
                    "filters_count": filters_count,
                    "active_filters": active_filters,
                    "notifications_count": notifications_count,
                    "gpt_messages_count": gpt_messages_count,
                    "days_left": days_left,
                    "expires_date": expires_date,
                    "status_badge": status_badge,
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

            # Count expired
            expired_count = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(
                        SniperUser.subscription_tier == 'trial',
                        SniperUser.trial_expires_at != None,
                        SniperUser.trial_expires_at < now
                    )
                )
            ) or 0
            tier_counts['expired'] = expired_count

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
            "activity_filter": activity,
            "sort": sort,
        })

    except Exception as e:
        logger.error(f"Users list error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


@app.get("/users/{user_id}", response_class=HTMLResponse)
async def user_detail(
    request: Request,
    user_id: int,
    username: str = Depends(verify_credentials)
):
    """Детальная информация о пользователе."""
    try:
        async with DatabaseSession() as session:
            user = await session.get(SniperUser, user_id)
            if not user:
                raise HTTPException(status_code=404, detail=f"Пользователь с ID {user_id} не найден")

            from sqlalchemy import case

            # Статистика фильтров (расширенная)
            filters_count = await session.scalar(
                select(func.count(SniperFilter.id)).where(SniperFilter.user_id == user.id)
            ) or 0
            active_filters = await session.scalar(
                select(func.count(SniperFilter.id)).where(
                    and_(SniperFilter.user_id == user.id, SniperFilter.is_active == True, SniperFilter.deleted_at == None)
                )
            ) or 0
            paused_filters = await session.scalar(
                select(func.count(SniperFilter.id)).where(
                    and_(SniperFilter.user_id == user.id, SniperFilter.is_active == False, SniperFilter.deleted_at == None)
                )
            ) or 0
            deleted_filters = await session.scalar(
                select(func.count(SniperFilter.id)).where(
                    and_(SniperFilter.user_id == user.id, SniperFilter.deleted_at != None)
                )
            ) or 0

            # Фильтры пользователя с stats
            filters_query = select(SniperFilter).where(SniperFilter.user_id == user.id)
            filters_result = await session.execute(filters_query)
            filters = filters_result.scalars().all()

            filters_data = []
            for f in filters:
                notif_count = await session.scalar(
                    select(func.count(SniperNotification.id)).where(
                        SniperNotification.filter_id == f.id
                    )
                ) or 0
                avg_score = await session.scalar(
                    select(func.avg(SniperNotification.score)).where(
                        SniperNotification.filter_id == f.id
                    )
                )
                last_notif_result = await session.scalar(
                    select(func.max(SniperNotification.sent_at)).where(
                        SniperNotification.filter_id == f.id
                    )
                )
                filters_data.append({
                    "filter": f,
                    "notif_count": notif_count,
                    "avg_score": avg_score,
                    "last_notif": last_notif_result,
                })

            # Расчёт оставшихся дней
            now = datetime.now()
            days_left = None
            if user.trial_expires_at:
                delta = user.trial_expires_at - now
                days_left = delta.days if delta.days >= 0 else -1

            # Events (activity timeline)
            try:
                events_query = (
                    select(UserEvent)
                    .where(UserEvent.user_id == user.id)
                    .order_by(UserEvent.created_at.desc())
                    .limit(50)
                )
                events_result = await session.execute(events_query)
                events = events_result.scalars().all()
            except Exception:
                events = []

            # Total notifications count
            notifications_total = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    SniperNotification.user_id == user.id
                )
            ) or 0

            # Notifications last 7 days
            week_ago = now - timedelta(days=7)
            notifications_week = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    and_(
                        SniperNotification.user_id == user.id,
                        SniperNotification.sent_at >= week_ago
                    )
                )
            ) or 0

            # AI analyses count — only notifications where match_info has real AI data
            ai_analyses_count = 0
            try:
                ai_notifs_query = (
                    select(SniperNotification).where(
                        and_(
                            SniperNotification.user_id == user.id,
                            SniperNotification.match_info != None
                        )
                    )
                )
                ai_notifs_result = await session.execute(ai_notifs_query)
                for n in ai_notifs_result.scalars().all():
                    mi = n.match_info if isinstance(n.match_info, dict) else {}
                    if any(mi.get(k) for k in ("ai_summary", "summary", "recommendation", "ai_recommendation")):
                        ai_analyses_count += 1
            except Exception:
                pass

            # Favorites count
            favorites_count = 0
            try:
                favorites_count = await session.scalar(
                    select(func.count(TenderFavorite.id)).where(TenderFavorite.user_id == user.id)
                ) or 0
            except Exception:
                pass

            # GPT sessions and messages
            gpt_sessions_data = []
            gpt_messages_total = 0
            try:
                gpt_sessions_query = (
                    select(GptSession)
                    .where(GptSession.user_id == user.id)
                    .order_by(GptSession.started_at.desc())
                    .limit(20)
                )
                gpt_result = await session.execute(gpt_sessions_query)
                gpt_sessions = gpt_result.scalars().all()

                for gs in gpt_sessions:
                    msg_count = await session.scalar(
                        select(func.count(GptMessage.id)).where(GptMessage.session_id == gs.id)
                    ) or 0
                    gpt_sessions_data.append({
                        "session": gs,
                        "message_count": msg_count,
                    })

                # Total GPT messages
                gpt_messages_total = await session.scalar(
                    select(func.count(GptMessage.id))
                    .select_from(GptMessage)
                    .join(GptSession, GptMessage.session_id == GptSession.id)
                    .where(GptSession.user_id == user.id)
                ) or 0
            except Exception:
                pass

            # GPT message limit (from tier)
            gpt_limits = {'trial': 10, 'starter': 0, 'pro': 50, 'premium': 200}
            gpt_limit = gpt_limits.get(user.subscription_tier, 10)

            # Recent notifications with details (last 20)
            recent_notifications = []
            try:
                rn_query = (
                    select(SniperNotification, SniperFilter.name.label('filter_name'))
                    .outerjoin(SniperFilter, SniperNotification.filter_id == SniperFilter.id)
                    .where(SniperNotification.user_id == user.id)
                    .order_by(SniperNotification.sent_at.desc())
                    .limit(20)
                )
                rn_result = await session.execute(rn_query)
                for row in rn_result.all():
                    notif = row[0]
                    fname = row[1]
                    # Check if user favorited this tender
                    is_favorited = await session.scalar(
                        select(func.count(TenderFavorite.id)).where(
                            and_(
                                TenderFavorite.user_id == user.id,
                                TenderFavorite.tender_number == notif.tender_number
                            )
                        )
                    ) or 0
                    has_ai = notif.match_info is not None and isinstance(notif.match_info, dict)
                    recent_notifications.append({
                        "notif": notif,
                        "filter_name": fname,
                        "is_favorited": is_favorited > 0,
                        "has_ai": has_ai,
                    })
            except Exception:
                pass

            # AI Analysis history — only notifications where match_info has actual AI data
            ai_analyses = []
            try:
                ai_query = (
                    select(SniperNotification)
                    .where(
                        and_(
                            SniperNotification.user_id == user.id,
                            SniperNotification.match_info != None
                        )
                    )
                    .order_by(SniperNotification.sent_at.desc())
                    .limit(50)
                )
                ai_result = await session.execute(ai_query)
                for notif in ai_result.scalars().all():
                    mi = notif.match_info if isinstance(notif.match_info, dict) else {}
                    summary = mi.get("ai_summary") or mi.get("summary") or ""
                    recommendation = mi.get("recommendation") or mi.get("ai_recommendation") or ""
                    # Skip entries without any actual AI data
                    if not summary and not recommendation:
                        continue
                    ai_analyses.append({
                        "notif": notif,
                        "summary": summary,
                        "risks": mi.get("risks") or mi.get("ai_risks") or "",
                        "recommendation": recommendation,
                        "confidence": mi.get("confidence") or mi.get("ai_confidence") or mi.get("relevance_score") or "",
                    })
                    if len(ai_analyses) >= 20:
                        break
            except Exception:
                pass

            # Payments
            payments_query = (
                select(Payment)
                .where(Payment.user_id == user.id)
                .order_by(Payment.created_at.desc())
            )
            payments_result = await session.execute(payments_query)
            payments = payments_result.scalars().all()

            payments_total_amount = await session.scalar(
                select(func.sum(Payment.amount)).where(
                    and_(Payment.user_id == user.id, Payment.status == 'succeeded')
                )
            ) or 0

            # Referrer
            referrer = None
            try:
                ref_result = await session.execute(
                    select(Referral).where(Referral.referred_id == user.id).limit(1)
                )
                ref = ref_result.scalar_one_or_none()
                if ref:
                    referrer = await session.get(SniperUser, ref.referrer_id)
            except Exception:
                pass

            # Registration source detection
            reg_source = "direct"
            if referrer:
                reg_source = "referral"
            elif user.data and isinstance(user.data, dict):
                reg_source = user.data.get("source", "direct")

        return templates.TemplateResponse("user_detail.html", {
            "request": request,
            "username": username,
            "user": user,
            "filters_count": filters_count,
            "active_filters": active_filters,
            "paused_filters": paused_filters,
            "deleted_filters": deleted_filters,
            "filters_data": filters_data,
            "days_left": days_left,
            "events": events,
            "notifications_total": notifications_total,
            "notifications_week": notifications_week,
            "ai_analyses_count": ai_analyses_count,
            "favorites_count": favorites_count,
            "gpt_sessions_data": gpt_sessions_data,
            "gpt_messages_total": gpt_messages_total,
            "gpt_limit": gpt_limit,
            "recent_notifications": recent_notifications,
            "ai_analyses": ai_analyses,
            "reg_source": reg_source,
            "payments": payments,
            "payments_total_amount": payments_total_amount,
            "referrer": referrer,
        })

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"User detail error: {e}", exc_info=True)
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
    valid_tiers = ['trial', 'starter', 'pro', 'premium']
    if tier not in valid_tiers:
        raise HTTPException(status_code=400, detail="Неверный тариф")

    try:
        limits_map = {
            'trial': {'filters': 3, 'notifications': 20, 'days': 7},
            'starter': {'filters': 5, 'notifications': 50, 'days': 30},
            'pro': {'filters': 15, 'notifications': 9999, 'days': 30},
            'premium': {'filters': 30, 'notifications': 9999, 'days': 30}
        }

        async with DatabaseSession() as session:
            # Получаем текущего пользователя
            user = await session.get(SniperUser, user_id)
            if not user:
                raise HTTPException(status_code=404, detail="Пользователь не найден")

            new_limits = limits_map[tier]
            now = datetime.now()

            # Вычисляем дату окончания подписки
            if tier in ['trial', 'starter', 'pro', 'premium']:
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
    """Добавить дни к подписке пользователя. user_id = sniper_users.id (внутренний ID)."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="Количество дней должно быть от 1 до 365")

    valid_tiers = ['trial', 'starter', 'pro', 'premium']
    if tier not in valid_tiers:
        tier = 'premium'

    try:
        limits_map = {
            'trial': {'filters': 3, 'notifications': 20},
            'starter': {'filters': 5, 'notifications': 50},
            'pro': {'filters': 15, 'notifications': 9999},
            'premium': {'filters': 30, 'notifications': 9999}
        }

        async with DatabaseSession() as session:
            # Ищем по внутреннему ID
            user = await session.get(SniperUser, user_id)
            if not user:
                raise HTTPException(status_code=404, detail=f"Пользователь с ID {user_id} не найден")

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

            logger.info(f"Added {days} days of {tier} to user {user.telegram_id} (id={user_id}), expires: {new_expires}")

        return RedirectResponse(url="/users", status_code=303)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Add days error for user_id={user_id}: {e}", exc_info=True)
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
            for tier in ['all', 'trial', 'starter', 'pro', 'premium']:
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
    valid_tiers = ['all', 'trial', 'starter', 'pro', 'premium']
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


@app.get("/broadcasts/{broadcast_id}", response_class=HTMLResponse)
async def broadcast_stats(request: Request, broadcast_id: int, username: str = Depends(verify_credentials)):
    """Show per-recipient stats for a specific broadcast."""
    from database import DatabaseSession, BroadcastMessage, BroadcastRecipient, SniperUser

    async with DatabaseSession() as session:
        bm = await session.scalar(
            select(BroadcastMessage).where(BroadcastMessage.id == broadcast_id)
        )
        if not bm:
            return HTMLResponse("Broadcast not found", status_code=404)

        result = await session.execute(
            select(BroadcastRecipient, SniperUser)
            .join(SniperUser, SniperUser.id == BroadcastRecipient.user_id)
            .where(BroadcastRecipient.broadcast_id == broadcast_id)
            .order_by(BroadcastRecipient.delivered_at.desc())
        )
        recipients = [(r, u) for r, u in result.all()]

        total = len(recipients)
        delivered = sum(1 for r, _ in recipients if r.status in ('delivered', 'clicked', 'converted', 'dismissed'))
        clicked = sum(1 for r, _ in recipients if r.status in ('clicked', 'converted'))
        converted = sum(1 for r, _ in recipients if r.status == 'converted')

        button_clicks = {}
        for r, _ in recipients:
            if r.clicked_button:
                button_clicks[r.clicked_button] = button_clicks.get(r.clicked_button, 0) + 1

    return templates.TemplateResponse("broadcast_stats.html", {
        "request": request,
        "broadcast": bm,
        "recipients": recipients,
        "total": total,
        "delivered": delivered,
        "clicked": clicked,
        "converted": converted,
        "button_clicks": button_clicks,
        "ctr_delivered": round(100 * delivered / total, 1) if total else 0,
        "ctr_clicked": round(100 * clicked / delivered, 1) if delivered else 0,
        "ctr_converted": round(100 * converted / clicked, 1) if clicked else 0,
    })


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
                    SniperUser.subscription_tier.in_(['starter', 'pro', 'premium', 'basic'])
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
# METRIKA (Яндекс Метрика)
# ============================================

@app.get("/metrika", response_class=HTMLResponse)
async def metrika_page(
    request: Request,
    period: int = Query(30, ge=7, le=90),
    username: str = Depends(verify_credentials)
):
    """Страница аналитики Яндекс Метрики."""
    try:
        if not metrika_service.is_configured:
            return templates.TemplateResponse("metrika.html", {
                "request": request,
                "username": username,
                "configured": False,
                "period": period,
            })

        date_to = datetime.now().strftime('%Y-%m-%d')
        date_from = (datetime.now() - timedelta(days=period)).strftime('%Y-%m-%d')

        summary = await metrika_service.get_traffic_summary(date_from, date_to)
        sources = await metrika_service.get_traffic_sources(date_from, date_to)
        conversions = await metrika_service.get_goal_conversions(date_from, date_to)
        ad_spend = await metrika_service.get_ad_spend(date_from, date_to)
        daily_visitors = await metrika_service.get_daily_visitors(date_from, date_to)

        # Registrations from DB for the same period
        async with DatabaseSession() as session:
            since = datetime.now() - timedelta(days=period)
            registrations_count = await session.scalar(
                select(func.count(SniperUser.id)).where(SniperUser.created_at >= since)
            ) or 0

            reg_query = (
                select(
                    func.date(SniperUser.created_at).label('date'),
                    func.count(SniperUser.id).label('count')
                )
                .where(SniperUser.created_at >= since)
                .group_by(func.date(SniperUser.created_at))
                .order_by(func.date(SniperUser.created_at))
            )
            reg_result = await session.execute(reg_query)
            daily_registrations = [
                {'date': str(row[0]), 'count': row[1]}
                for row in reg_result.all()
            ]

        return templates.TemplateResponse("metrika.html", {
            "request": request,
            "username": username,
            "configured": True,
            "period": period,
            "summary": summary,
            "sources": sources,
            "conversions": conversions,
            "ad_spend": ad_spend,
            "daily_visitors": daily_visitors,
            "daily_registrations": daily_registrations,
            "registrations_count": registrations_count,
        })

    except Exception as e:
        logger.error(f"Metrika page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# ECONOMICS (Юнит-экономика)
# ============================================

@app.get("/economics", response_class=HTMLResponse)
async def economics_page(
    request: Request,
    period: int = Query(30, ge=7, le=90),
    username: str = Depends(verify_credentials)
):
    """Страница юнит-экономики."""
    try:
        now = datetime.now()
        since = now - timedelta(days=period)

        async with DatabaseSession() as session:
            # Total users
            total_users = await session.scalar(select(func.count(SniperUser.id))) or 0

            # New users in period
            new_users = await session.scalar(
                select(func.count(SniperUser.id)).where(SniperUser.created_at >= since)
            ) or 0

            # Revenue in period
            total_revenue = await session.scalar(
                select(func.sum(Payment.amount)).where(
                    and_(Payment.status == 'succeeded', Payment.created_at >= since)
                )
            ) or 0

            # Total revenue ever
            total_revenue_all = await session.scalar(
                select(func.sum(Payment.amount)).where(Payment.status == 'succeeded')
            ) or 0

            # Paying users
            paying_users = await session.scalar(
                select(func.count(distinct(Payment.user_id))).where(Payment.status == 'succeeded')
            ) or 0

            # Ad spend from Metrika
            date_from = since.strftime('%Y-%m-%d')
            date_to = now.strftime('%Y-%m-%d')
            ad_spend = 0.0
            if metrika_service.is_configured:
                ad_spend = await metrika_service.get_ad_spend(date_from, date_to)

            # CAC
            cac = (ad_spend / new_users) if new_users > 0 else 0

            # ARPU
            arpu = (total_revenue_all / total_users) if total_users > 0 else 0

            # ARPPU
            arppu = (total_revenue_all / paying_users) if paying_users > 0 else 0

            # Average lifetime (months) — compatible with both SQLite and PostgreSQL
            lifetime_users_query = (
                select(SniperUser.created_at, SniperUser.last_activity)
                .where(SniperUser.last_activity.isnot(None))
            )
            lifetime_result = await session.execute(lifetime_users_query)
            lifetime_rows = lifetime_result.all()
            if lifetime_rows:
                total_days = sum(
                    (row[1] - row[0]).total_seconds() / 86400
                    for row in lifetime_rows if row[0] and row[1]
                )
                avg_lifetime_days = total_days / len(lifetime_rows)
            else:
                avg_lifetime_days = 30
            avg_lifetime_months = max(avg_lifetime_days / 30, 1)

            # LTV
            ltv = arpu * avg_lifetime_months

            # Monthly ARPU for payback
            monthly_arpu = (total_revenue / max(total_users, 1)) * (30 / max(period, 1))
            payback_months = (cac / monthly_arpu) if monthly_arpu > 0 else 0

            # MRR
            starter_count = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(SniperUser.subscription_tier.in_(['starter', 'basic']), SniperUser.trial_expires_at > now)
                )
            ) or 0
            pro_count = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(SniperUser.subscription_tier == 'pro', SniperUser.trial_expires_at > now)
                )
            ) or 0
            premium_count = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(SniperUser.subscription_tier == 'premium', SniperUser.trial_expires_at > now)
                )
            ) or 0
            mrr = starter_count * TARIFF_PRICES['starter'] + pro_count * TARIFF_PRICES['pro'] + premium_count * TARIFF_PRICES['premium']

            # Churn rate
            period_start = since
            active_start = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(SniperUser.created_at < period_start, SniperUser.last_activity >= period_start - timedelta(days=period))
                )
            ) or 0
            churned = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(
                        SniperUser.created_at < period_start,
                        SniperUser.last_activity < period_start,
                        SniperUser.last_activity >= period_start - timedelta(days=period)
                    )
                )
            ) or 0
            churn_rate = (churned / active_start * 100) if active_start > 0 else 0

            metrics = {
                "cac": cac,
                "arpu": arpu,
                "arppu": arppu,
                "ltv": ltv,
                "payback_months": payback_months,
                "mrr": mrr,
                "churn_rate": churn_rate,
                "active_start": active_start,
                "churned": churned,
            }

            # Daily revenue
            revenue_query = (
                select(
                    func.date(Payment.created_at).label('date'),
                    func.sum(Payment.amount).label('amount')
                )
                .where(and_(Payment.status == 'succeeded', Payment.created_at >= since))
                .group_by(func.date(Payment.created_at))
                .order_by(func.date(Payment.created_at))
            )
            rev_result = await session.execute(revenue_query)
            daily_revenue_raw = {str(row[0]): float(row[1]) for row in rev_result.all()}

            # Fill missing dates
            daily_revenue = []
            for i in range(period):
                d = (since + timedelta(days=i)).strftime('%Y-%m-%d')
                daily_revenue.append({"date": d, "amount": daily_revenue_raw.get(d, 0)})

            # Feedback stats (notification quality)
            feedback_stats = []
            try:
                fav_sub = (
                    select(
                        UserFeedback.filter_id,
                        func.count(UserFeedback.id).label('cnt')
                    )
                    .where(UserFeedback.feedback_type == 'interesting')
                    .group_by(UserFeedback.filter_id)
                ).subquery()

                hidden_sub = (
                    select(
                        UserFeedback.filter_id,
                        func.count(UserFeedback.id).label('cnt')
                    )
                    .where(UserFeedback.feedback_type.in_(['hidden', 'irrelevant']))
                    .group_by(UserFeedback.filter_id)
                ).subquery()

                # Get keywords from filters with feedback
                filters_with_fb = (
                    select(
                        SniperFilter.id,
                        SniperFilter.keywords,
                        func.coalesce(fav_sub.c.cnt, 0).label('favorites'),
                        func.coalesce(hidden_sub.c.cnt, 0).label('hidden'),
                    )
                    .outerjoin(fav_sub, SniperFilter.id == fav_sub.c.filter_id)
                    .outerjoin(hidden_sub, SniperFilter.id == hidden_sub.c.filter_id)
                    .where(
                        (func.coalesce(fav_sub.c.cnt, 0) + func.coalesce(hidden_sub.c.cnt, 0)) > 0
                    )
                )
                fb_result = await session.execute(filters_with_fb)

                kw_stats = {}
                for row in fb_result.all():
                    keywords_data = row[1]
                    if not keywords_data:
                        continue
                    kw_list = keywords_data if isinstance(keywords_data, list) else [kw.strip() for kw in str(keywords_data).split(',')]
                    for kw in kw_list:
                        kw = str(kw).strip().lower()
                        if not kw:
                            continue
                        if kw not in kw_stats:
                            kw_stats[kw] = {"favorites": 0, "hidden": 0}
                        kw_stats[kw]["favorites"] += row[2]
                        kw_stats[kw]["hidden"] += row[3]

                for kw, stats in sorted(kw_stats.items(), key=lambda x: -(x[1]["favorites"] + x[1]["hidden"])):
                    total = stats["favorites"] + stats["hidden"]
                    engagement = round(stats["favorites"] / total * 100) if total > 0 else 0
                    feedback_stats.append({
                        "keyword": kw,
                        "favorites": stats["favorites"],
                        "hidden": stats["hidden"],
                        "total": total,
                        "engagement": engagement,
                    })
                feedback_stats = feedback_stats[:20]
            except Exception as e:
                logger.warning(f"Feedback stats error: {e}")

            # Alerts
            alerts = []
            # Registration drop
            week_new = await session.scalar(
                select(func.count(SniperUser.id)).where(SniperUser.created_at >= now - timedelta(days=7))
            ) or 0
            prev_week_new = await session.scalar(
                select(func.count(SniperUser.id)).where(
                    and_(
                        SniperUser.created_at >= now - timedelta(days=14),
                        SniperUser.created_at < now - timedelta(days=7)
                    )
                )
            ) or 0
            if prev_week_new > 0 and week_new < prev_week_new * 0.5:
                alerts.append({
                    "level": "danger",
                    "message": f"Регистрации упали более чем на 50%: {week_new} vs {prev_week_new} на прошлой неделе"
                })

            # Zero payments for 3 days
            recent_payments = await session.scalar(
                select(func.count(Payment.id)).where(
                    and_(Payment.status == 'succeeded', Payment.created_at >= now - timedelta(days=3))
                )
            ) or 0
            if recent_payments == 0 and paying_users > 0:
                alerts.append({
                    "level": "warning",
                    "message": "0 платежей за последние 3 дня"
                })

            # Churn spike
            if churn_rate > 30:
                alerts.append({
                    "level": "danger",
                    "message": f"Высокий отток: {churn_rate:.1f}% за период"
                })

        return templates.TemplateResponse("economics.html", {
            "request": request,
            "username": username,
            "period": period,
            "metrics": metrics,
            "daily_revenue": daily_revenue,
            "feedback_stats": feedback_stats,
            "alerts": alerts,
        })

    except Exception as e:
        logger.error(f"Economics page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# API: User notifications (lazy-load)
# ============================================

@app.get("/api/users/{user_id}/notifications")
async def api_user_notifications(
    user_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    username: str = Depends(verify_credentials)
):
    """API для подгрузки уведомлений пользователя."""
    try:
        offset = (page - 1) * per_page
        async with DatabaseSession() as session:
            total = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    SniperNotification.user_id == user_id
                )
            ) or 0
            total_pages = (total + per_page - 1) // per_page

            query = (
                select(
                    SniperNotification,
                    SniperFilter.name.label('filter_name')
                )
                .outerjoin(SniperFilter, SniperNotification.filter_id == SniperFilter.id)
                .where(SniperNotification.user_id == user_id)
                .order_by(SniperNotification.sent_at.desc())
                .offset(offset)
                .limit(per_page)
            )
            result = await session.execute(query)
            rows = result.all()

            items = []
            for row in rows:
                notif = row[0]
                filter_name = row[1]
                items.append({
                    "tender_number": notif.tender_number,
                    "tender_name": notif.tender_name,
                    "tender_price": float(notif.tender_price) if notif.tender_price else None,
                    "score": notif.score,
                    "filter_name": filter_name,
                    "sent_at": notif.sent_at.strftime('%d.%m.%Y %H:%M') if notif.sent_at else None,
                })

        return JSONResponse({
            "items": items,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": total_pages,
        })

    except Exception as e:
        logger.error(f"API user notifications error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# API: GPT session messages (lazy-load)
# ============================================

@app.get("/api/gpt-sessions/{session_id}/messages")
async def api_gpt_session_messages(
    session_id: str,
    username: str = Depends(verify_credentials)
):
    """API для подгрузки сообщений GPT-сессии."""
    try:
        async with DatabaseSession() as session:
            messages_query = (
                select(GptMessage)
                .where(GptMessage.session_id == session_id)
                .order_by(GptMessage.created_at)
            )
            result = await session.execute(messages_query)
            messages = result.scalars().all()

            items = []
            for msg in messages:
                items.append({
                    "role": msg.role,
                    "content": msg.content[:2000] if msg.content else "",
                    "tool_name": msg.tool_name,
                    "tool_args": msg.tool_args,
                    "tool_result": (msg.tool_result[:500] if msg.tool_result else None),
                    "created_at": msg.created_at.strftime('%d.%m.%Y %H:%M:%S') if msg.created_at else None,
                })

        return JSONResponse({"items": items})

    except Exception as e:
        logger.error(f"API GPT session messages error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# COHORT ANALYTICS PAGE
# ============================================

@app.get("/admin/cohorts", response_class=HTMLResponse)
async def cohorts_page(
    request: Request,
    days: int = Query(30, ge=7, le=90),
    username: str = Depends(verify_credentials)
):
    """Когортная аналитика — конверсионная воронка по дням регистрации."""
    try:
        from collections import defaultdict

        async with DatabaseSession() as session:
            since = datetime.now() - timedelta(days=days)

            # All users registered since
            users_query = (
                select(SniperUser)
                .where(SniperUser.created_at >= since)
                .order_by(SniperUser.created_at)
            )
            users_result = await session.execute(users_query)
            users = users_result.scalars().all()

            # Group by registration date
            cohort_data = defaultdict(lambda: {
                "registered": 0,
                "created_filter": 0,
                "got_notification": 0,
                "used_gpt": 0,
                "used_ai_analysis": 0,
                "converted_paid": 0,
            })

            for u in users:
                day_key = u.created_at.strftime('%d.%m.%Y') if u.created_at else "N/A"
                cohort_data[day_key]["registered"] += 1

                # Created filter?
                has_filter = await session.scalar(
                    select(func.count(SniperFilter.id)).where(SniperFilter.user_id == u.id)
                ) or 0
                if has_filter > 0:
                    cohort_data[day_key]["created_filter"] += 1

                # Got notification?
                has_notif = await session.scalar(
                    select(func.count(SniperNotification.id)).where(SniperNotification.user_id == u.id)
                ) or 0
                if has_notif > 0:
                    cohort_data[day_key]["got_notification"] += 1

                # Used GPT?
                try:
                    has_gpt = await session.scalar(
                        select(func.count(GptSession.id)).where(GptSession.user_id == u.id)
                    ) or 0
                    if has_gpt > 0:
                        cohort_data[day_key]["used_gpt"] += 1
                except Exception:
                    pass

                # Used AI analysis? (has match_info)
                has_ai = await session.scalar(
                    select(func.count(SniperNotification.id)).where(
                        and_(
                            SniperNotification.user_id == u.id,
                            SniperNotification.match_info != None
                        )
                    )
                ) or 0
                if has_ai > 0:
                    cohort_data[day_key]["used_ai_analysis"] += 1

                # Converted to paid?
                if u.subscription_tier in ('starter', 'pro', 'premium', 'basic'):
                    cohort_data[day_key]["converted_paid"] += 1

            # Convert to sorted list
            cohorts = []
            for day_key in sorted(cohort_data.keys(), key=lambda x: datetime.strptime(x, '%d.%m.%Y')):
                cohorts.append({
                    "date": day_key,
                    **cohort_data[day_key],
                })

        # Calculate totals in Python (more reliable than Jinja2 namespace)
        totals = {"reg": 0, "filter": 0, "notif": 0, "gpt": 0, "ai": 0, "paid": 0}
        for c in cohorts:
            totals["reg"] += c.get("registered", 0)
            totals["filter"] += c.get("created_filter", 0)
            totals["notif"] += c.get("got_notification", 0)
            totals["gpt"] += c.get("used_gpt", 0)
            totals["ai"] += c.get("used_ai_analysis", 0)
            totals["paid"] += c.get("converted_paid", 0)

        return templates.TemplateResponse("cohorts.html", {
            "request": request,
            "username": username,
            "cohorts": cohorts,
            "totals": totals,
            "days": days,
        })

    except Exception as e:
        logger.error(f"Cohorts page error: {e}", exc_info=True)
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": str(e)
        })


# ============================================
# API: Cohort analysis
# ============================================

@app.get("/api/economics/cohorts")
async def api_cohorts(
    period: int = Query(30, ge=7, le=90),
    username: str = Depends(verify_credentials)
):
    """API для когортной таблицы удержания."""
    try:
        now = datetime.now()
        max_weeks = min(period // 7, 8)

        async with DatabaseSession() as session:
            # Get all users created in the last N weeks
            cohort_start = now - timedelta(weeks=max_weeks + 1)
            users_query = (
                select(SniperUser.id, SniperUser.created_at, SniperUser.last_activity)
                .where(SniperUser.created_at >= cohort_start)
                .order_by(SniperUser.created_at)
            )
            result = await session.execute(users_query)
            users = result.all()

        if not users:
            return JSONResponse({"cohorts": [], "max_week": 0})

        from collections import defaultdict

        # Group users by registration week
        cohorts = defaultdict(list)
        for user_id, created_at, last_activity in users:
            week_start = created_at - timedelta(days=created_at.weekday())
            week_key = week_start.strftime('%Y-%m-%d')
            cohorts[week_key].append((created_at, last_activity))

        result_cohorts = []
        for week_key in sorted(cohorts.keys()):
            users_in_cohort = cohorts[week_key]
            cohort_size = len(users_in_cohort)
            if cohort_size == 0:
                continue

            week_start_date = datetime.strptime(week_key, '%Y-%m-%d')
            retention = []

            for w in range(max_weeks + 1):
                week_end = week_start_date + timedelta(weeks=w + 1)
                if week_end > now:
                    retention.append(None)
                    continue

                week_boundary = week_start_date + timedelta(weeks=w)
                active_count = sum(
                    1 for created, last_act in users_in_cohort
                    if last_act and last_act >= week_boundary
                )
                pct = round(active_count / cohort_size * 100)
                retention.append(pct)

            result_cohorts.append({
                "label": week_key,
                "size": cohort_size,
                "retention": retention,
            })

        return JSONResponse({
            "cohorts": result_cohorts[-8:],
            "max_week": max_weeks,
        })

    except Exception as e:
        logger.error(f"Cohorts API error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# EXPORT: Economics CSV
# ============================================

@app.get("/export/economics")
async def export_economics_csv(
    period: int = Query(30, ge=7, le=90),
    username: str = Depends(verify_credentials)
):
    """Экспорт экономических метрик в CSV."""
    try:
        now = datetime.now()
        since = now - timedelta(days=period)

        async with DatabaseSession() as session:
            total_users = await session.scalar(select(func.count(SniperUser.id))) or 0
            new_users = await session.scalar(
                select(func.count(SniperUser.id)).where(SniperUser.created_at >= since)
            ) or 0
            total_revenue = await session.scalar(
                select(func.sum(Payment.amount)).where(
                    and_(Payment.status == 'succeeded', Payment.created_at >= since)
                )
            ) or 0
            paying_users = await session.scalar(
                select(func.count(distinct(Payment.user_id))).where(Payment.status == 'succeeded')
            ) or 0

            ad_spend = 0.0
            if metrika_service.is_configured:
                ad_spend = await metrika_service.get_ad_spend(
                    since.strftime('%Y-%m-%d'), now.strftime('%Y-%m-%d')
                )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Metric', 'Value', 'Period'])
        writer.writerow(['Total Users', total_users, f'{period}d'])
        writer.writerow(['New Users', new_users, f'{period}d'])
        writer.writerow(['Revenue (RUB)', total_revenue, f'{period}d'])
        writer.writerow(['Paying Users', paying_users, 'all time'])
        writer.writerow(['Ad Spend (RUB)', ad_spend, f'{period}d'])
        writer.writerow(['CAC (RUB)', round(ad_spend / new_users, 2) if new_users > 0 else 0, f'{period}d'])
        writer.writerow(['ARPU (RUB)', round(total_revenue / total_users, 2) if total_users > 0 else 0, f'{period}d'])
        writer.writerow(['ARPPU (RUB)', round(total_revenue / paying_users, 2) if paying_users > 0 else 0, f'{period}d'])

        output.seek(0)
        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=economics_{datetime.now().strftime('%Y%m%d')}.csv"}
        )

    except Exception as e:
        logger.error(f"Export economics error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


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
    valid_tiers = ['starter', 'pro', 'premium']
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
    'starter': {'filters': 5, 'notifications': 50, 'price': 499},
    'pro': {'filters': 15, 'notifications': 9999, 'price': 1490},
    'premium': {'filters': 30, 'notifications': 9999, 'price': 2990},
    'ai_unlimited': {'filters': 0, 'notifications': 0, 'price': 1490},
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
# LOGS VIEWER
# ============================================

@app.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    lines: int = Query(100, ge=10, le=1000),
    level: str = Query("all"),
    username: str = Depends(verify_credentials)
):
    """Просмотр логов бота в реальном времени."""
    log_files = [
        Path(__file__).parent.parent.parent / "bot" / "bot.log",
        Path(__file__).parent.parent / "service.log",
    ]

    logs_content = []

    for log_file in log_files:
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                    all_lines = f.readlines()
                    # Берём последние N строк
                    recent_lines = all_lines[-lines:]

                    # Фильтруем по уровню
                    if level != "all":
                        level_upper = level.upper()
                        recent_lines = [l for l in recent_lines if level_upper in l]

                    logs_content.append({
                        "file": log_file.name,
                        "lines": recent_lines
                    })
            except Exception as e:
                logs_content.append({
                    "file": log_file.name,
                    "lines": [f"Error reading log: {e}"]
                })

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "username": username,
        "logs": logs_content,
        "current_lines": lines,
        "current_level": level,
    })


@app.get("/api/logs/stream")
async def logs_stream(
    lines: int = Query(50, ge=10, le=500),
    username: str = Depends(verify_credentials)
):
    """API для получения последних логов (для автообновления)."""
    log_file = Path(__file__).parent.parent.parent / "bot" / "bot.log"

    if not log_file.exists():
        return JSONResponse({"lines": [], "error": "Log file not found"})

    try:
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
            recent_lines = all_lines[-lines:]

        return JSONResponse({
            "lines": [l.strip() for l in recent_lines],
            "total": len(all_lines)
        })
    except Exception as e:
        return JSONResponse({"lines": [], "error": str(e)})


# ============================================
# HEALTH DASHBOARD
# ============================================

@app.get("/health-dashboard", response_class=HTMLResponse)
async def health_dashboard(
    request: Request,
    username: str = Depends(verify_credentials)
):
    """Детальный статус всех сервисов."""
    services = {}

    # 1. Database check
    try:
        async with DatabaseSession() as session:
            await session.execute(select(func.count(SniperUser.id)))
        services['database'] = {'status': 'ok', 'message': 'Connected'}
    except Exception as e:
        services['database'] = {'status': 'error', 'message': str(e)}

    # 2. Bot status (check if bot.log was updated recently)
    bot_log = Path(__file__).parent.parent.parent / "bot" / "bot.log"
    if bot_log.exists():
        mtime = datetime.fromtimestamp(bot_log.stat().st_mtime)
        age_minutes = (datetime.now() - mtime).total_seconds() / 60
        if age_minutes < 5:
            services['bot'] = {'status': 'ok', 'message': f'Active (log updated {age_minutes:.0f}m ago)'}
        else:
            services['bot'] = {'status': 'warning', 'message': f'Possibly inactive ({age_minutes:.0f}m since last log)'}
    else:
        services['bot'] = {'status': 'unknown', 'message': 'Log file not found'}

    # 3. Tender Sniper service
    service_log = Path(__file__).parent.parent / "service.log"
    if service_log.exists():
        mtime = datetime.fromtimestamp(service_log.stat().st_mtime)
        age_minutes = (datetime.now() - mtime).total_seconds() / 60
        if age_minutes < 10:
            services['sniper_service'] = {'status': 'ok', 'message': f'Active ({age_minutes:.0f}m ago)'}
        else:
            services['sniper_service'] = {'status': 'warning', 'message': f'Possibly inactive ({age_minutes:.0f}m)'}
    else:
        services['sniper_service'] = {'status': 'unknown', 'message': 'Service log not found'}

    # 4. Check external APIs
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as client:
            async with client.get("https://zakupki.gov.ru") as resp:
                if resp.status == 200:
                    services['zakupki_api'] = {'status': 'ok', 'message': 'Available'}
                else:
                    services['zakupki_api'] = {'status': 'warning', 'message': f'HTTP {resp.status}'}
    except Exception as e:
        services['zakupki_api'] = {'status': 'error', 'message': f'Unavailable: {str(e)[:50]}'}

    # 5. Disk space
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        free_gb = free // (1024 ** 3)
        used_percent = (used / total) * 100
        if free_gb > 5:
            services['disk'] = {'status': 'ok', 'message': f'{free_gb} GB free ({used_percent:.1f}% used)'}
        elif free_gb > 1:
            services['disk'] = {'status': 'warning', 'message': f'{free_gb} GB free ({used_percent:.1f}% used)'}
        else:
            services['disk'] = {'status': 'error', 'message': f'Low disk space: {free_gb} GB'}
    except Exception as e:
        services['disk'] = {'status': 'unknown', 'message': str(e)}

    # 6. Recent notifications count (delivery_status column doesn't exist)
    try:
        async with DatabaseSession() as session:
            one_hour_ago = datetime.now() - timedelta(hours=1)
            recent_count = await session.scalar(
                select(func.count(SniperNotification.id)).where(
                    SniperNotification.sent_at >= one_hour_ago,
                )
            ) or 0

            if recent_count > 0:
                services['notifications'] = {'status': 'ok', 'message': f'{recent_count} sent in last hour'}
            else:
                services['notifications'] = {'status': 'ok', 'message': 'No notifications in last hour'}
    except Exception as e:
        services['notifications'] = {'status': 'unknown', 'message': str(e)}

    return templates.TemplateResponse("health_dashboard.html", {
        "request": request,
        "username": username,
        "services": services,
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })


# ============================================
# TRIGGER MONITORING
# ============================================

@app.post("/trigger-monitoring")
async def trigger_monitoring(
    background_tasks: BackgroundTasks,
    username: str = Depends(verify_credentials)
):
    """Ручной запуск мониторинга тендеров."""
    try:
        # Импортируем сервис мониторинга
        from tender_sniper.service import TenderSniperService

        async def run_monitoring():
            try:
                service = TenderSniperService(
                    bot_token=TELEGRAM_BOT_TOKEN,
                    poll_interval=300,
                    max_tenders_per_poll=50
                )
                await service.initialize()
                await service.run_single_poll()
                logger.info("Manual monitoring poll completed")
            except Exception as e:
                logger.error(f"Manual monitoring error: {e}", exc_info=True)

        background_tasks.add_task(run_monitoring)

        return JSONResponse({
            "status": "ok",
            "message": "Monitoring triggered. Check logs for results."
        })
    except Exception as e:
        logger.error(f"Trigger monitoring error: {e}", exc_info=True)
        return JSONResponse({
            "status": "error",
            "message": str(e)
        }, status_code=500)


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
            customer_email = (
                metadata.get('customer_email')
                or metadata.get('email')
                or (obj.get('receipt') or {}).get('customer', {}).get('email')
            )

            if not telegram_id or not tier:
                logger.warning(f"Missing metadata in webhook: {data}")
                return JSONResponse({'status': 'error', 'message': 'Missing metadata'})

            if tier not in ('starter', 'pro', 'premium'):
                logger.warning(f"Unknown tier in webhook: {tier}")
                return JSONResponse({'status': 'error', 'message': f'Unknown tier: {tier}'})

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
                    'starter': {'filters': 5, 'notifications': 50},
                    'pro': {'filters': 15, 'notifications': 9999},
                    'premium': {'filters': 30, 'notifications': 9999},
                }
                new_expires = now + timedelta(days=30)

                main_values = {
                    'subscription_tier': tier,
                    'trial_expires_at': new_expires,
                    'filters_limit': limits_map[tier]['filters'],
                    'notifications_limit': limits_map[tier]['notifications'],
                }
                if customer_email:
                    main_values['email'] = customer_email

                await session.execute(
                    update(SniperUser)
                    .where(SniperUser.id == user.id)
                    .values(**main_values)
                )

                # Multi-account upgrade: find siblings by email and upgrade if stronger
                if customer_email:
                    from bot.utils.tier_priority import should_upgrade
                    siblings_result = await session.execute(
                        select(SniperUser).where(
                            SniperUser.email == customer_email,
                            SniperUser.id != user.id,
                        )
                    )
                    siblings = siblings_result.scalars().all()
                    for sibling in siblings:
                        if should_upgrade(
                            current_tier=sibling.subscription_tier,
                            current_expires=sibling.trial_expires_at,
                            new_tier=tier,
                            new_expires=new_expires,
                        ):
                            await session.execute(
                                update(SniperUser)
                                .where(SniperUser.id == sibling.id)
                                .values(
                                    subscription_tier=tier,
                                    trial_expires_at=new_expires,
                                    filters_limit=limits_map[tier]['filters'],
                                    notifications_limit=limits_map[tier]['notifications'],
                                )
                            )
                            logger.info(
                                f"Multi-account upgrade: sibling user {sibling.id} "
                                f"(tg={sibling.telegram_id}) → {tier}"
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
                await session.flush()

                # Broadcast conversion attribution
                try:
                    from bot.handlers.broadcast_tracking import attribute_conversion
                    await attribute_conversion(user_id=user.id, payment_id=payment_record.id)
                except Exception as attr_err:
                    logger.error(f"Broadcast attribution error: {attr_err}")

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
