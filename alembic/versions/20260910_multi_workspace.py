"""multi-workspace: company_id on filters/notifications, active_company_id
on web_sessions, relax company_members to (user_id, company_id)

Revision ID: 20260910_multi_workspace
Revises: 20260717_kp
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa


revision = '20260910_multi_workspace'
down_revision = '20260717_kp'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. New columns (nullable — backfilled below; stays nullable for any
    #    user who has never had a cabinet company).
    op.add_column('sniper_filters', sa.Column('company_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_sniper_filters_company_id_companies',
        'sniper_filters', 'companies', ['company_id'], ['id'],
    )
    op.create_index('ix_sniper_filters_company_id', 'sniper_filters', ['company_id'])

    op.add_column('sniper_notifications', sa.Column('company_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_sniper_notifications_company_id_companies',
        'sniper_notifications', 'companies', ['company_id'], ['id'],
    )
    op.create_index('ix_sniper_notifications_company_id', 'sniper_notifications', ['company_id'])

    op.add_column('web_sessions', sa.Column('active_company_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        'fk_web_sessions_active_company_id_companies',
        'web_sessions', 'companies', ['active_company_id'], ['id'],
    )

    # 2. Relax "one company per user" so a user can belong to more than one
    #    company (still at most once per company).
    op.drop_constraint('uq_company_members_user', 'company_members', type_='unique')
    op.create_unique_constraint(
        'uq_company_members_user_company', 'company_members', ['user_id', 'company_id'],
    )

    # 3. Backfill. Every user who owns at least one filter gets a company —
    #    reusing an existing membership if they have one, otherwise creating
    #    one exactly like team_service.get_or_create_company_for_user does
    #    lazily today (name = 'Команда {first_name or f"User {id}"}',
    #    role='owner'). Then filters/notifications for that user get
    #    company_id set.
    bind = op.get_bind()

    filter_owner_ids = [row[0] for row in bind.execute(
        sa.text('SELECT DISTINCT user_id FROM sniper_filters WHERE company_id IS NULL')
    ).fetchall()]

    for user_id in filter_owner_ids:
        existing = bind.execute(
            sa.text('SELECT company_id FROM company_members WHERE user_id = :uid LIMIT 1'),
            {'uid': user_id},
        ).fetchone()

        if existing:
            company_id = existing[0]
        else:
            name_row = bind.execute(
                sa.text('SELECT first_name FROM sniper_users WHERE id = :uid'),
                {'uid': user_id},
            ).fetchone()
            name_base = (name_row[0] if name_row and name_row[0] else f'User {user_id}')
            company_id = bind.execute(
                sa.text(
                    "INSERT INTO companies (name, owner_user_id, created_at) "
                    "VALUES (:name, :uid, now()) RETURNING id"
                ),
                {'name': f'Команда {name_base}', 'uid': user_id},
            ).scalar()
            bind.execute(
                sa.text(
                    "INSERT INTO company_members (company_id, user_id, role, joined_at) "
                    "VALUES (:cid, :uid, 'owner', now())"
                ),
                {'cid': company_id, 'uid': user_id},
            )

        bind.execute(
            sa.text('UPDATE sniper_filters SET company_id = :cid WHERE user_id = :uid AND company_id IS NULL'),
            {'cid': company_id, 'uid': user_id},
        )
        bind.execute(
            sa.text('UPDATE sniper_notifications SET company_id = :cid WHERE user_id = :uid AND company_id IS NULL'),
            {'cid': company_id, 'uid': user_id},
        )

    # Notifications from users with no filters at all (e.g. instant-search-
    # only users) — backfill from an existing membership if they have one.
    # If they don't, company_id stays NULL (they've never used the cabinet
    # team features, nothing regresses).
    remaining = [row[0] for row in bind.execute(
        sa.text('SELECT DISTINCT user_id FROM sniper_notifications WHERE company_id IS NULL')
    ).fetchall()]
    for user_id in remaining:
        existing = bind.execute(
            sa.text('SELECT company_id FROM company_members WHERE user_id = :uid LIMIT 1'),
            {'uid': user_id},
        ).fetchone()
        if existing:
            bind.execute(
                sa.text('UPDATE sniper_notifications SET company_id = :cid WHERE user_id = :uid AND company_id IS NULL'),
                {'cid': existing[0], 'uid': user_id},
            )


def downgrade() -> None:
    # Lossy if any user has already joined a second company (that user
    # would violate the restored single-company constraint) — acceptable
    # for a downgrade of a not-yet-used feature.
    op.drop_constraint('uq_company_members_user_company', 'company_members', type_='unique')
    op.create_unique_constraint('uq_company_members_user', 'company_members', ['user_id'])

    op.drop_constraint('fk_web_sessions_active_company_id_companies', 'web_sessions', type_='foreignkey')
    op.drop_column('web_sessions', 'active_company_id')

    op.drop_index('ix_sniper_notifications_company_id', table_name='sniper_notifications')
    op.drop_constraint('fk_sniper_notifications_company_id_companies', 'sniper_notifications', type_='foreignkey')
    op.drop_column('sniper_notifications', 'company_id')

    op.drop_index('ix_sniper_filters_company_id', table_name='sniper_filters')
    op.drop_constraint('fk_sniper_filters_company_id_companies', 'sniper_filters', type_='foreignkey')
    op.drop_column('sniper_filters', 'company_id')
