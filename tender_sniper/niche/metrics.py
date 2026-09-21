"""Индекс привлекательности ниши и чтение витрины eis.niche_metrics.

Витрина отдаёт сырые метрики, здесь они сводятся в одно число 0–100.
Веса и константы вынесены в конфиг (`IndexConfig`, переопределяется
переменными окружения) — по ТЗ они не должны быть зашиты в код, потому
что настраиваются по результатам, а не выводятся из теории.

Четыре составляющие:

  C (конкуренция) — чем меньше заявок, тем лучше. Экспонента, а не
      линейка: разница между 1 и 2 заявками куда важнее, чем между
      8 и 9.
  M (маржа)       — чем меньше роняют цену, тем больше остаётся нам.
  V (объём)       — логарифм: ниша из 50 процедур и из 500 отличаются
      меньше, чем из 1 и из 10.
  O (открытость)  — HHI по победителям. Мало участников может означать
      не свободное поле, а одного окопавшегося поставщика; без этой
      составляющей такие ниши возглавляли бы рейтинг.
"""
import csv
import logging
import math
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger("niche.metrics")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "")) if os.getenv(name) else default
    except ValueError:
        logger.warning("%s не число, берём значение по умолчанию %s", name, default)
        return default


@dataclass(frozen=True)
class IndexConfig:
    """Веса и константы индекса. Значения по умолчанию — из ТЗ."""
    weight_competition: float = field(
        default_factory=lambda: _env_float("NICHE_W_COMPETITION", 0.35))
    weight_margin: float = field(
        default_factory=lambda: _env_float("NICHE_W_MARGIN", 0.30))
    weight_volume: float = field(
        default_factory=lambda: _env_float("NICHE_W_VOLUME", 0.20))
    weight_openness: float = field(
        default_factory=lambda: _env_float("NICHE_W_OPENNESS", 0.15))

    # 1 заявка → 100, 3 → ~33, 6 → ~6
    competition_decay: float = field(
        default_factory=lambda: _env_float("NICHE_COMPETITION_DECAY", 0.55))
    # Снижение 20% и больше съедает маржу полностью
    margin_drop_limit: float = field(
        default_factory=lambda: _env_float("NICHE_MARGIN_DROP_LIMIT", 0.20))
    # Объём, при котором ниша считается полноценной
    volume_saturation: float = field(
        default_factory=lambda: _env_float("NICHE_VOLUME_SATURATION", 50))
    # HHI 0.25 и выше — ниша схвачена
    hhi_limit: float = field(
        default_factory=lambda: _env_float("NICHE_HHI_LIMIT", 0.25))
    # Срезы мельче этого в рейтинг не идут — шум
    min_procedures: int = field(
        default_factory=lambda: int(_env_float("NICHE_MIN_PROCEDURES", 5)))


DEFAULT_CONFIG = IndexConfig()


def herfindahl(inns: Sequence[str]) -> Optional[float]:
    """Индекс Херфиндаля: сумма квадратов долей. 1.0 — весь срез у
    одного поставщика, около нуля — рынок раздроблен."""
    values = [i for i in (inns or []) if i]
    if not values:
        return None
    total = len(values)
    return sum((count / total) ** 2 for count in Counter(values).values())


def top_n_share(inns: Sequence[str], n: int = 3) -> Optional[float]:
    values = [i for i in (inns or []) if i]
    if not values:
        return None
    counts = [c for _, c in Counter(values).most_common(n)]
    return sum(counts) / len(values)


def repeat_customers_share(inns: Sequence[str]) -> Optional[float]:
    """Доля закупок у заказчиков, приходивших за этим не один раз.
    Высокая доля означает регулярный спрос, а не разовую прихоть."""
    values = [i for i in (inns or []) if i]
    if not values:
        return None
    counts = Counter(values)
    repeat = sum(c for c in counts.values() if c >= 2)
    return repeat / len(values)


def score_competition(median_bids: Optional[float], cfg: IndexConfig) -> float:
    """Без данных о заявках ниша не может считаться привлекательной:
    неизвестность — не то же самое, что отсутствие конкурентов."""
    if median_bids is None:
        return 0.0
    return 100.0 * math.exp(-cfg.competition_decay * max(0.0, median_bids - 1))


def score_margin(median_drop: Optional[float], cfg: IndexConfig) -> float:
    if median_drop is None:
        return 0.0
    # Отрицательное снижение (цену подняли) маржу не увеличивает.
    drop = max(0.0, median_drop)
    return 100.0 * max(0.0, 1 - drop / cfg.margin_drop_limit)


def score_volume(procedures_count: Optional[int], cfg: IndexConfig) -> float:
    count = procedures_count or 0
    if count <= 0:
        return 0.0
    return 100.0 * min(1.0, math.log1p(count) / math.log1p(cfg.volume_saturation))


def score_openness(hhi: Optional[float], cfg: IndexConfig) -> float:
    """Победители неизвестны — считаем нишу открытой, а не закрытой:
    отсутствие данных не доказывает монополию, а занижать индекс по
    свежим срезам, где контракты ещё не заключены, было бы неверно.
    Это допущение видно в выгрузке по колонке known_winners."""
    if hhi is None:
        return 100.0
    return 100.0 * (1 - min(1.0, hhi / cfg.hhi_limit))


def compute_index(row: Dict, cfg: IndexConfig = DEFAULT_CONFIG) -> Dict:
    """Дополняет строку витрины составляющими и итоговым индексом."""
    hhi = row.get("winner_hhi")
    if hhi is None:
        hhi = herfindahl(row.get("winner_inns") or [])

    components = {
        "score_competition": score_competition(row.get("median_bids"), cfg),
        "score_margin": score_margin(row.get("median_drop"), cfg),
        "score_volume": score_volume(row.get("procedures_count"), cfg),
        "score_openness": score_openness(hhi, cfg),
    }
    index = (cfg.weight_competition * components["score_competition"]
             + cfg.weight_margin * components["score_margin"]
             + cfg.weight_volume * components["score_volume"]
             + cfg.weight_openness * components["score_openness"])

    enriched = dict(row)
    enriched.update(components)
    enriched["winner_hhi"] = hhi
    enriched["top3_winner_share"] = top_n_share(row.get("winner_inns") or [])
    enriched["repeat_customers"] = repeat_customers_share(row.get("customer_inns") or [])
    enriched["index"] = round(index, 1)
    return enriched


def rank(rows: Iterable[Dict], cfg: IndexConfig = DEFAULT_CONFIG):
    """Делит срезы на рейтинг и «мало данных».

    Мелкие срезы не выбрасываем совсем: по одной-двум процедурам медиана
    ничего не значит и в рейтинге создаёт ложные вершины, но сама ниша
    может быть интересной — просто про неё пока нечего сказать.
    """
    ranked, sparse = [], []
    for row in rows:
        enriched = compute_index(row, cfg)
        target = ranked if (row.get("procedures_count") or 0) >= cfg.min_procedures else sparse
        target.append(enriched)
    ranked.sort(key=lambda r: r["index"], reverse=True)
    sparse.sort(key=lambda r: r.get("procedures_count") or 0, reverse=True)
    return ranked, sparse


CSV_COLUMNS = [
    "okpd2_level", "okpd2", "region", "price_bucket", "procedures_count",
    "median_bids", "share_single_bid", "share_zero_bid", "median_drop",
    "p90_drop", "median_nmck", "unique_winners", "known_winners",
    "winner_hhi", "top3_winner_share", "repeat_customers",
    "score_competition", "score_margin", "score_volume", "score_openness",
    "index", "first_seen", "last_seen",
]


def write_csv(rows: List[Dict], path: str) -> None:
    """Выгрузка для проверки глазами — по ТЗ это kill-criterion этапа:
    топ-30 сверяется с карточками на сайте ЕИС, и только потом UI."""
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS,
                                extrasaction="ignore", delimiter=";")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in row.items() if k in CSV_COLUMNS})
