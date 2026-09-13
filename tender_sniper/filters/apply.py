"""Идемпотентный импортёр filters_v2.yaml → sniper_filters.

Использование:
    python -m tender_sniper.filters.apply --config filters_v2.yaml
        [--apply] [--only slug1,slug2] [--wave 1] [--company-id 57]
        [--user-nikolai 1] [--user-artem 105]
        [--group-chat-id -1004376206306] [--notify-thread-id 230]

По умолчанию — dry-run: только печатает diff, ничего не пишет. Реальная
запись — только с явным --apply. Перед записью обязателен успешный бэкап
в backups/filters_YYYYMMDD_HHMMSS.json; без него импорт не стартует.

Апгрейд старых (ещё без slug) строк на первый прогон: `migration_map` даёт
старый номер тендерного фильтра ("№075" -> id=75) и список новых slug,
которые его заменяют. ПЕРВЫЙ slug в списке "усыновляет" старую строку
(сохраняет id, match_count, last_match_at, notify_chat_ids как есть,
просто обновляет декларативные поля и проставляет slug) — остальные
slug из того же списка создаются как новые строки. После первого прогона
все строки уже адресуются по slug напрямую, старые номера больше не нужны.

`deprecated` — старые фильтры архивируются (status=archived, is_active=
False), никогда не удаляются физически (FK на pipeline_cards всё равно
не позволит, если на фильтр есть карточки — см. docs/filters_v2_discovery.md).
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy import select, update as sa_update

from database import DatabaseSession, SniperFilter
from tender_sniper.filters.config import load_config, ResolvedFilter, ResolvedConfig, ConfigError

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(__file__).parent.parent.parent / 'backups'

# Декларативные поля, которыми владеет импортёр — сравниваются для diff'а
# и перезаписываются при --apply. Всё остальное на строке (user_id,
# company_id, notify_chat_ids/notify_thread_id для УЖЕ существующих строк,
# match_count, last_match_at, error_count) — эксплуатационное состояние,
# импортёр его не трогает.
OWNED_FIELDS = [
    'name', 'keywords', 'exclusions', 'price_min', 'price_max',
    'regions', 'object_type', 'group', 'owner', 'wave', 'status',
    'nacrejim', 'notes',
]


def _db_field_name(config_field: str) -> str:
    return {
        'exclusions': 'exclude_keywords',
        'object_type': 'tender_types',
        'group': 'group_id',
    }.get(config_field, config_field)


def _current_value(row: Dict, config_field: str):
    db_field = _db_field_name(config_field)
    if config_field == 'object_type':
        types = row.get('tender_types') or []
        return types[0] if types else None
    return row.get(db_field)


def _target_value(rf: ResolvedFilter, config_field: str):
    if config_field == 'object_type':
        return rf.object_type
    return getattr(rf, config_field)


def _old_number(legacy_key: str) -> Optional[int]:
    """"№075" -> 75. Возвращает None, если формат не распознан (не ошибка —
    просто эта запись не резолвится по номеру, только по slug при повторных прогонах)."""
    digits = ''.join(ch for ch in legacy_key if ch.isdigit())
    return int(digits) if digits else None


async def _load_current_rows(session, company_id: int) -> Dict[int, Dict]:
    """Все строки фильтров компании — и по id, и (если уже проставлен) по slug."""
    result = await session.execute(
        select(SniperFilter).where(SniperFilter.company_id == company_id)
    )
    rows = {}
    for f in result.scalars().all():
        rows[f.id] = {
            'id': f.id, 'slug': f.slug, 'name': f.name,
            'keywords': f.keywords or [], 'exclude_keywords': f.exclude_keywords or [],
            'price_min': f.price_min, 'price_max': f.price_max,
            'regions': f.regions or [], 'tender_types': f.tender_types or [],
            'group_id': f.group_id, 'owner': f.owner, 'wave': f.wave,
            'status': f.status, 'nacrejim': f.nacrejim, 'notes': f.notes,
            'is_active': f.is_active,
        }
    return rows


def _backup(rows_by_id: Dict[int, Dict], config_version: int) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    path = BACKUP_DIR / f'filters_{ts}.json'
    payload = {
        'backed_up_at': datetime.utcnow().isoformat(),
        'config_version': config_version,
        'filters': list(rows_by_id.values()),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


_ORDER_INSENSITIVE_FIELDS = {'keywords', 'exclusions', 'regions'}


def _values_differ(cf: str, old, new) -> bool:
    """Списки ключевиков/исключений/регионов сравниваются как множества —
    порядок в YAML/пресете не несёт смысла для матчинга, и сравнение по
    порядку давало бы шумные ложные "изменения" (например, тот же пресет
    CORE в другом порядке после пересборки регионов)."""
    if cf in _ORDER_INSENSITIVE_FIELDS:
        return set(old or []) != set(new or [])
    return old != new


def _diff_lines(slug: str, current: Optional[Dict], target: ResolvedFilter, action: str) -> List[str]:
    lines = []
    if action == 'create':
        lines.append(f"+ создан: {slug} ({target.name})")
        return lines
    if action == 'archive':
        lines.append(f"− архивирован: {slug}")
        return lines
    # changed / unchanged
    field_diffs = []
    for cf in OWNED_FIELDS:
        old = _current_value(current, cf)
        new = _target_value(target, cf)
        if _values_differ(cf, old, new):
            field_diffs.append(f"    {cf}: {old!r} -> {new!r}")
    if field_diffs:
        lines.append(f"~ изменён: {slug} ({target.name})")
        lines.extend(field_diffs)
    return lines


async def run(config_path: Path, apply: bool, only: Optional[List[str]], wave: Optional[int],
              company_id: int, user_nikolai: int, user_artem: int,
              telegram_nikolai: int, telegram_artem: int,
              group_chat_id: int, notify_thread_id: int) -> int:
    try:
        cfg: ResolvedConfig = load_config(config_path)
    except ConfigError as e:
        print(f"ОШИБКА КОНФИГА: {e}")
        return 1

    owner_user_id = {'nikolai': user_nikolai, 'artem': user_artem}
    owner_telegram_id = {'nikolai': telegram_nikolai, 'artem': telegram_artem}

    target_filters = cfg.filters
    if only:
        only_set = set(only)
        target_filters = [f for f in target_filters if f.slug in only_set]
    if wave is not None:
        target_filters = [f for f in target_filters if f.wave == wave]

    async with DatabaseSession() as session:
        current_rows = await _load_current_rows(session, company_id)
        by_slug = {r['slug']: r for r in current_rows.values() if r['slug']}
        by_id = current_rows

        if apply:
            backup_path = _backup(current_rows, cfg.version)
            print(f"Бэкап: {backup_path}")

        # legacy-номер -> slug, который "усыновляет" старую строку (первый в списке)
        adopt_target_by_old_number: Dict[int, str] = {}
        for old_key, new_slugs in cfg.migration_map.items():
            n = _old_number(old_key)
            if n is not None and new_slugs:
                adopt_target_by_old_number[n] = new_slugs[0]

        diff_out: List[str] = []
        touched_ids: set = set()

        # 1) deprecated — архивация старых строк по номеру
        for dep in cfg.deprecated:
            n = _old_number(dep.id)
            if n is None or n not in by_id:
                continue
            row = by_id[n]
            if row['status'] == 'archived' and not row['is_active']:
                continue  # уже архивирован — идемпотентно, без диффа
            diff_out.extend(_diff_lines(dep.id, row, None, 'archive'))
            touched_ids.add(n)
            if apply:
                await session.execute(
                    sa_update(SniperFilter).where(SniperFilter.id == n).values(
                        status='archived', is_active=False, updated_by=user_nikolai,
                    )
                )

        # 2) фильтры конфига — upsert по slug (или "усыновление" старой строки по номеру)
        for rf in target_filters:
            existing = by_slug.get(rf.slug)
            adopt_row_id = None
            if existing is None:
                for old_n, adopt_slug in adopt_target_by_old_number.items():
                    if adopt_slug == rf.slug and old_n in by_id and old_n not in touched_ids:
                        adopt_row_id = old_n
                        existing = by_id[old_n]
                        break

            action = 'update' if existing else 'create'
            diff_out.extend(_diff_lines(rf.slug, existing, rf, action))

            if not apply:
                continue

            values = dict(
                name=rf.name, keywords=rf.keywords, exclude_keywords=rf.exclusions,
                price_min=rf.price_min, price_max=rf.price_max, regions=rf.regions,
                tender_types=[rf.object_type], group_id=rf.group, owner=rf.owner,
                wave=rf.wave, status=rf.status, is_active=(rf.status == 'active'),
                nacrejim=rf.nacrejim, notes=rf.notes,
                slug=rf.slug, config_version=cfg.version, updated_by=user_nikolai,
            )
            if existing:
                row_id = adopt_row_id if adopt_row_id is not None else existing['id']
                touched_ids.add(row_id)
                await session.execute(
                    sa_update(SniperFilter).where(SniperFilter.id == row_id).values(**values)
                )
            else:
                # Новая строка — маршрутизация уведомлений задаётся один раз,
                # при создании (владелец лично + общая группа/тема); для уже
                # существующих строк notify_chat_ids/notify_thread_id не трогаем.
                values.update(
                    user_id=owner_user_id[rf.owner],
                    company_id=company_id,
                    notify_chat_ids=[owner_telegram_id[rf.owner], group_chat_id],
                    notify_thread_id=notify_thread_id,
                )
                session.add(SniperFilter(**values))

    if diff_out:
        print('\n'.join(diff_out))
    else:
        print("Изменений нет (конфиг уже применён).")

    n_created = sum(1 for l in diff_out if l.startswith('+'))
    n_changed = sum(1 for l in diff_out if l.startswith('~'))
    n_archived = sum(1 for l in diff_out if l.startswith('−'))
    print(f"\nИтого: создано {n_created}, изменено {n_changed}, архивировано {n_archived}"
          f" ({'применено' if apply else 'DRY-RUN, ничего не записано'})")
    return 0


def main():
    parser = argparse.ArgumentParser(description='Импорт filters_v2.yaml в sniper_filters')
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--apply', action='store_true', help='реально писать в БД (иначе dry-run)')
    parser.add_argument('--dry-run', action='store_true', help='явный no-op, поведение по умолчанию')
    parser.add_argument('--only', type=str, default=None, help='slug1,slug2,...')
    parser.add_argument('--wave', type=int, default=None)
    parser.add_argument('--company-id', type=int, default=57)
    parser.add_argument('--user-nikolai', type=int, default=1)
    parser.add_argument('--user-artem', type=int, default=105)
    parser.add_argument('--telegram-nikolai', type=int, default=298437198)
    parser.add_argument('--telegram-artem', type=int, default=264225761)
    parser.add_argument('--group-chat-id', type=int, default=-1004376206306)
    parser.add_argument('--notify-thread-id', type=int, default=230)
    args = parser.parse_args()

    only = args.only.split(',') if args.only else None
    apply = args.apply and not args.dry_run

    code = asyncio.run(run(
        config_path=args.config, apply=apply, only=only, wave=args.wave,
        company_id=args.company_id, user_nikolai=args.user_nikolai, user_artem=args.user_artem,
        telegram_nikolai=args.telegram_nikolai, telegram_artem=args.telegram_artem,
        group_chat_id=args.group_chat_id, notify_thread_id=args.notify_thread_id,
    ))
    sys.exit(code)


if __name__ == '__main__':
    main()
