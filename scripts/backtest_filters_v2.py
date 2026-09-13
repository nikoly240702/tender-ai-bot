"""Бэктест filters_v2.yaml по живому поиску на zakupki.gov.ru (Этап 4 ТЗ).

ВАЖНО — это ВЫБОРОЧНЫЙ, не исчерпывающий прогон: zakupki.gov.ru не
поддерживает OR-запрос из нескольких фраз за один вызов, а в конфиге
1266 ключевиков на 50 фильтров — прогнать все означало бы тысячи живых
запросов через прокси, который живёт ~135 запросов до бана. Поэтому на
фильтр берётся выборка из N САМЫХ ДЛИННЫХ (обычно самых специфичных)
ключевиков, а не весь список. Цифры в отчёте — оценка по выборке, не
точный подсчёт; их снижает то, что часть номенклатуры не запрашивалась,
и по-другому это не измерить без полноценного архива (см.
docs/filters_v2_discovery.md, п.5).

Использование:
    python -m scripts.backtest_filters_v2 [--config filters_v2.yaml]
        [--sample-keywords 5] [--days 30] [--max-tenders 15]
        [--wave 1] [--only slug1,slug2] [--sleep 1.5]

Требует прокси-доступ к zakupki.gov.ru — если PROXY_URL* не заданы в
окружении (как в локальном .env), запускать через:
    railway run --service tender-ai-bot python -m scripts.backtest_filters_v2
"""

import argparse
import asyncio
import random
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from tender_sniper.filters.config import load_config, ResolvedFilter, ConfigError
from tender_sniper.instant_search import InstantSearch

REPORTS_DIR = Path(__file__).parent.parent / 'reports'


def pick_sample_keywords(keywords: List[str], n: int) -> List[str]:
    """N самых длинных (обычно самых специфичных, меньше шанс общего слова
    типа "насос") ключевиков фильтра."""
    return sorted(keywords, key=len, reverse=True)[:n]


async def backtest_one_filter(searcher: InstantSearch, rf: ResolvedFilter,
                              sample_n: int, days: int, max_tenders: int) -> Dict:
    sample_kw = pick_sample_keywords(rf.keywords, sample_n)
    filter_data = {
        'id': rf.slug,
        'name': rf.name,
        'keywords': sample_kw,
        'exclude_keywords': rf.exclusions,
        'price_min': rf.price_min,
        'price_max': rf.price_max,
        'regions': rf.regions,
        'tender_types': [rf.object_type],
        'purchase_stage': 'all',  # и активные, и завершённые — без этого
                                   # "submission" по умолчанию исключает архив
    }
    try:
        result = await searcher.search_by_filter(
            filter_data, max_tenders=max_tenders, use_ai_check=False,
            date_from_days=days,
        )
    except Exception as e:
        return {'slug': rf.slug, 'name': rf.name, 'owner': rf.owner, 'wave': rf.wave,
                'sample_keywords': sample_kw, 'error': str(e)}

    matches = result.get('matches', [])
    prices = [m['price'] for m in matches if m.get('price')]
    customers = Counter(
        (m.get('customer_name') or m.get('customer') or '—').strip()
        for m in matches
    )
    n = len(matches)
    below_100k = sum(1 for p in prices if p < 100_000)
    quartiles = statistics.quantiles(sorted(prices), n=4) if len(prices) >= 4 else None
    examples = random.sample(matches, min(20, n)) if n else []

    return {
        'slug': rf.slug, 'name': rf.name, 'owner': rf.owner, 'wave': rf.wave,
        'sample_keywords': sample_kw, 'n_keywords_total': len(rf.keywords),
        'count': n, 'per_day': n / days if days else 0,
        'median_price': statistics.median(prices) if prices else None,
        'quartiles': quartiles,
        'share_below_100k': (below_100k / len(prices) * 100) if prices else None,
        'top_customers': customers.most_common(10),
        'examples': examples,
    }


def fmt_money(v: Optional[float]) -> str:
    if v is None:
        return '—'
    return f"{v:,.0f}".replace(',', ' ') + ' ₽'


def render_report(results: List[Dict], cfg_version: int, days: int, sample_n: int) -> str:
    ok = [r for r in results if 'error' not in r]
    errored = [r for r in results if 'error' in r]

    red_flags = [r for r in ok if r['count'] == 0 or r['per_day'] > 150]
    total_by_owner = Counter()
    for r in ok:
        total_by_owner[r['owner']] += r['count']

    lines = []
    lines.append(f"# Бэктест filters_v2.yaml (v{cfg_version}) — {datetime.utcnow():%Y-%m-%d}")
    lines.append("")
    lines.append(f"**Метод:** выборочный живой поиск на zakupki.gov.ru, по {sample_n} самым "
                 f"длинным ключевикам на фильтр (не по всем — см. шапку скрипта), окно "
                 f"публикации {days} дней, все стадии (активные + завершённые).")
    lines.append("**Это не точный подсчёт** — реальный поток при полном наборе ключевиков "
                 "будет выше, особенно у фильтров с длинным списком.")
    lines.append("")
    lines.append(f"Прогнано фильтров: {len(results)} · успешно: {len(ok)} · ошибок: {len(errored)}")
    lines.append("")
    lines.append("## Сводка по владельцам (оценка/выборка, не точный поток)")
    lines.append("")
    for owner, total in total_by_owner.most_common():
        per_day = total / days if days else 0
        lines.append(f"- **{owner}**: {total} совпадений за {days} дн. (~{per_day:.1f}/день)")
    lines.append("")

    if red_flags:
        lines.append("## 🚩 Красные флаги")
        lines.append("")
        for r in red_flags:
            reason = "0 совпадений за период" if r['count'] == 0 else f"{r['per_day']:.0f}/день > 150"
            lines.append(f"- `{r['slug']}` ({r['name']}, {r['owner']}, wave {r['wave']}) — {reason}")
        lines.append("")

    if errored:
        lines.append("## Ошибки запроса")
        lines.append("")
        for r in errored:
            lines.append(f"- `{r['slug']}`: {r['error']}")
        lines.append("")

    lines.append("## По фильтрам")
    lines.append("")
    for r in sorted(ok, key=lambda x: -x['count']):
        lines.append(f"### `{r['slug']}` — {r['name']}")
        lines.append(f"владелец: {r['owner']} · волна: {r['wave']} · "
                     f"выборка {len(r['sample_keywords'])}/{r['n_keywords_total']} ключевиков")
        lines.append("")
        lines.append(f"- Совпадений за {days} дн.: **{r['count']}** (~{r['per_day']:.1f}/день)")
        lines.append(f"- Медиана НМЦК: {fmt_money(r['median_price'])}")
        if r['quartiles']:
            q1, q2, q3 = r['quartiles']
            lines.append(f"- Квартили НМЦК: Q1={fmt_money(q1)} · Q2={fmt_money(q2)} · Q3={fmt_money(q3)}")
        if r['share_below_100k'] is not None:
            lines.append(f"- Доля лотов < 100 000 ₽: {r['share_below_100k']:.0f}%")
        if r['top_customers']:
            top_str = ', '.join(f"{c} ({n})" for c, n in r['top_customers'][:10])
            lines.append(f"- Топ заказчиков: {top_str}")
        lines.append(f"- Ключевики выборки: {', '.join(r['sample_keywords'])}")
        if r['examples']:
            lines.append("")
            lines.append("  Примеры:")
            for ex in r['examples']:
                name = (ex.get('name') or '—')[:100]
                price = fmt_money(ex.get('price'))
                region = ex.get('region') or ex.get('customer_region') or '—'
                lines.append(f"  - {name} · {price} · {region}")
        lines.append("")

    return '\n'.join(lines)


async def main():
    parser = argparse.ArgumentParser(description='Бэктест filters_v2.yaml по zakupki.gov.ru')
    parser.add_argument('--config', type=Path, default=Path('filters_v2.yaml'))
    parser.add_argument('--sample-keywords', type=int, default=5)
    parser.add_argument('--days', type=int, default=30)
    parser.add_argument('--max-tenders', type=int, default=15)
    parser.add_argument('--wave', type=int, default=None)
    parser.add_argument('--only', type=str, default=None)
    parser.add_argument('--sleep', type=float, default=1.5, help='пауза между фильтрами, сек')
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"ОШИБКА КОНФИГА: {e}")
        sys.exit(1)

    targets = cfg.filters
    if args.only:
        only_set = set(args.only.split(','))
        targets = [f for f in targets if f.slug in only_set]
    if args.wave is not None:
        targets = [f for f in targets if f.wave == args.wave]

    print(f"Бэктест {len(targets)} фильтров (выборка {args.sample_keywords} ключевиков, "
          f"{args.days} дн., stage=all)...")

    searcher = InstantSearch()
    results = []
    for i, rf in enumerate(targets, 1):
        print(f"  [{i}/{len(targets)}] {rf.slug}...")
        r = await backtest_one_filter(searcher, rf, args.sample_keywords, args.days, args.max_tenders)
        results.append(r)
        if 'error' in r:
            print(f"      ⚠️ {r['error']}")
        else:
            print(f"      {r['count']} совпадений")
        if i < len(targets):
            await asyncio.sleep(args.sleep)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"backtest_{datetime.utcnow():%Y%m%d}.md"
    report_path.write_text(
        render_report(results, cfg.version, args.days, args.sample_keywords),
        encoding='utf-8',
    )
    print(f"\nОтчёт: {report_path}")


if __name__ == '__main__':
    asyncio.run(main())
