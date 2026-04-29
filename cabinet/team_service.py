"""Доменная логика team workspace.

Все функции принимают/возвращают plain dicts. Сессия БД создаётся через
DatabaseSession (как в database/sqlalchemy_adapter.py).
"""

import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from database import (
    DatabaseSession, SniperUser, Company, CompanyMember, TeamInvite,
    PipelineCard, PipelineCardHistory,
)

logger = logging.getLogger(__name__)

INVITE_TTL_DAYS = 7
INVITE_DEFAULT_MAX_USES = 10


def _company_dict(company: Company) -> Dict:
    return {
        'id': company.id,
        'name': company.name,
        'owner_user_id': company.owner_user_id,
        'created_at': company.created_at,
    }


def _member_dict(member: CompanyMember) -> Dict:
    return {
        'id': member.id,
        'company_id': member.company_id,
        'user_id': member.user_id,
        'role': member.role,
        'joined_at': member.joined_at,
    }


def _invite_dict(invite: TeamInvite) -> Dict:
    return {
        'id': invite.id,
        'company_id': invite.company_id,
        'token': invite.token,
        'created_by': invite.created_by,
        'created_at': invite.created_at,
        'expires_at': invite.expires_at,
        'revoked_at': invite.revoked_at,
        'max_uses': invite.max_uses,
        'used_count': invite.used_count,
    }


async def get_company_for_user(user_id: int) -> Optional[Dict]:
    """Возвращает company юзера или None если не в команде."""
    async with DatabaseSession() as session:
        membership = await session.scalar(
            select(CompanyMember).where(CompanyMember.user_id == user_id)
        )
        if not membership:
            return None
        company = await session.get(Company, membership.company_id)
        return _company_dict(company) if company else None


async def get_or_create_company_for_user(user_id: int) -> Dict:
    """Возвращает существующую company юзера или создаёт новую с ним как owner."""
    existing = await get_company_for_user(user_id)
    if existing:
        return existing

    async with DatabaseSession() as session:
        user = await session.get(SniperUser, user_id)
        name_base = user.first_name if user and user.first_name else f'User {user_id}'
        company = Company(name=f'Команда {name_base}', owner_user_id=user_id)
        session.add(company)
        await session.flush()
        member = CompanyMember(company_id=company.id, user_id=user_id, role='owner')
        session.add(member)
        await session.commit()
        return _company_dict(company)


async def list_members(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(CompanyMember).where(CompanyMember.company_id == company_id)
            .order_by(CompanyMember.joined_at)
        )
        return [_member_dict(m) for m in result.scalars().all()]


async def list_members_with_users(company_id: int) -> List[Dict]:
    """То же что list_members, но с полем display_name из sniper_users."""
    async with DatabaseSession() as session:
        result = await session.execute(
            select(CompanyMember, SniperUser).join(
                SniperUser, SniperUser.id == CompanyMember.user_id
            ).where(CompanyMember.company_id == company_id)
            .order_by(CompanyMember.joined_at)
        )
        out = []
        for m, u in result.all():
            d = _member_dict(m)
            d['display_name'] = u.first_name or u.username or f'User {u.id}'
            d['username'] = u.username
            d['telegram_id'] = u.telegram_id
            out.append(d)
        return out


# ============================================
# Invite tokens
# ============================================

async def create_invite(company_id: int, created_by: int,
                        max_uses: int = INVITE_DEFAULT_MAX_USES) -> Dict:
    async with DatabaseSession() as session:
        token = secrets.token_urlsafe(24)
        invite = TeamInvite(
            company_id=company_id,
            token=token,
            created_by=created_by,
            expires_at=datetime.utcnow() + timedelta(days=INVITE_TTL_DAYS),
            max_uses=max_uses,
            used_count=0,
        )
        session.add(invite)
        await session.commit()
        return _invite_dict(invite)


async def validate_invite_token(token: str) -> Optional[Dict]:
    """Возвращает invite если валиден (не отозван, не expired, есть свободные uses)."""
    async with DatabaseSession() as session:
        invite = await session.scalar(select(TeamInvite).where(TeamInvite.token == token))
        if not invite:
            return None
        if invite.revoked_at is not None:
            return None
        if invite.expires_at < datetime.utcnow():
            return None
        if invite.used_count >= invite.max_uses:
            return None
        return _invite_dict(invite)


async def revoke_invite(invite_id: int, by_user_id: int) -> bool:
    async with DatabaseSession() as session:
        invite = await session.get(TeamInvite, invite_id)
        if not invite:
            return False
        company = await session.get(Company, invite.company_id)
        if not company or company.owner_user_id != by_user_id:
            return False
        invite.revoked_at = datetime.utcnow()
        await session.commit()
        return True


async def list_active_invites(company_id: int) -> List[Dict]:
    async with DatabaseSession() as session:
        result = await session.execute(
            select(TeamInvite).where(
                TeamInvite.company_id == company_id,
                TeamInvite.revoked_at.is_(None),
                TeamInvite.expires_at > datetime.utcnow(),
            ).order_by(TeamInvite.created_at.desc())
        )
        return [_invite_dict(i) for i in result.scalars().all()]


async def accept_invite(token: str, user_id: int) -> Dict:
    """Принять инвайт. Возвращает {ok, error?, company_id?, already?}."""
    invite = await validate_invite_token(token)
    if not invite:
        return {'ok': False, 'error': 'Ссылка недействительна или истекла'}

    existing_company = await get_company_for_user(user_id)
    if existing_company:
        if existing_company['id'] == invite['company_id']:
            return {'ok': True, 'company_id': existing_company['id'], 'already': True}
        return {
            'ok': False,
            'error': f"Already in another team ({existing_company['name']}). Сначала покиньте её.",
        }

    async with DatabaseSession() as session:
        member = CompanyMember(
            company_id=invite['company_id'], user_id=user_id, role='member',
        )
        session.add(member)
        await session.execute(
            update(TeamInvite).where(TeamInvite.id == invite['id'])
            .values(used_count=TeamInvite.used_count + 1)
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return {'ok': False, 'error': 'Уже состоите в команде'}

    return {'ok': True, 'company_id': invite['company_id']}


# ============================================
# Member management
# ============================================

async def remove_member(company_id: int, target_user_id: int, by_user_id: int) -> Dict:
    """Owner-only: удаляет члена. Owner себя не может удалить.
    Карточки удаляемого члена переназначаются на owner."""
    async with DatabaseSession() as session:
        company = await session.get(Company, company_id)
        if not company:
            return {'ok': False, 'error': 'Команда не найдена'}
        if company.owner_user_id != by_user_id:
            return {'ok': False, 'error': 'Только owner может удалять членов'}
        if target_user_id == company.owner_user_id:
            return {'ok': False, 'error': 'Owner не может удалить сам себя'}

        membership = await session.scalar(
            select(CompanyMember).where(
                CompanyMember.company_id == company_id,
                CompanyMember.user_id == target_user_id,
            )
        )
        if not membership:
            return {'ok': False, 'error': 'Член не найден'}

        # Переназначить карточки на owner
        await session.execute(
            update(PipelineCard).where(
                PipelineCard.company_id == company_id,
                PipelineCard.assignee_user_id == target_user_id,
            ).values(assignee_user_id=company.owner_user_id)
        )
        # История: добавить запись для каждой переназначенной (упрощённо — без перебора)
        # Решено в плане: одна общая запись о removal не делается, история по каждой карточке
        # опционально. В MVP пропускаем дет. историю — owner и так знает что удалил.
        await session.delete(membership)
        await session.commit()
        return {'ok': True}


async def leave_team(user_id: int) -> Dict:
    """Member выходит из команды. Owner не может."""
    async with DatabaseSession() as session:
        membership = await session.scalar(
            select(CompanyMember).where(CompanyMember.user_id == user_id)
        )
        if not membership:
            return {'ok': False, 'error': 'Вы не в команде'}
        company = await session.get(Company, membership.company_id)
        if company and company.owner_user_id == user_id:
            return {'ok': False, 'error': 'Owner не может покинуть команду'}

        # Переназначить карточки покидающего на owner
        if company:
            await session.execute(
                update(PipelineCard).where(
                    PipelineCard.company_id == company.id,
                    PipelineCard.assignee_user_id == user_id,
                ).values(assignee_user_id=company.owner_user_id)
            )
        await session.delete(membership)
        await session.commit()
        return {'ok': True}
