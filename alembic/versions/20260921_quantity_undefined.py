"""Процедуры с неопределённым объёмом ломали метрику снижения цены.

Найдено на первых же реальных данных: среднее «снижение» по нишам было
от −360% до −10 765%, а отдельные «победители» предлагали 78 млрд при
НМЦК 1,45 млн.

Причина не в парсере. При неопределённом объёме закупки (ч. 24 ст. 42
44-ФЗ) участники торгуются не ценой контракта, а СУММОЙ ЦЕН ЗА ЕДИНИЦУ
товара. Это другая величина, и сравнивать её с НМЦК бессмысленно:
получается отрицательное снижение в тысячи процентов, которое тянет за
собой медиану всей ниши.

В извещении такие процедуры помечены `quantityUndefined = true`. Ловим
флаг, кладём в eis.procedure и исключаем такие процедуры из метрик
снижения — но НЕ из подсчёта процедур и заявок: конкуренция в них
измеряется нормально.

Заодно отсекаются остатки: снижение вне диапазона 0–100% (цена выросла
относительно НМЦК) в медиану не идёт, но считается отдельно, чтобы
аномалия была видна, а не спрятана.

Revision ID: 20260921_qty_undef
Revises: 20260921_niche_mv
Create Date: 2026-09-21

"""
from alembic import op
import sqlalchemy as sa


revision = '20260921_qty_undef'
down_revision = '20260921_niche_mv'
branch_labels = None
depends_on = None

SCHEMA = 'eis'

VIEW = """
CREATE MATERIALIZED VIEW eis.niche_metrics AS
WITH base AS (
    SELECT
        pr.purchase_number,
        pr.okpd2_primary,
        pr.customer_region_code                       AS region,
        pr.customer_inn,
        pr.nmck,
        pr.published_at,
        pr.quantity_undefined,
        p.bids_submitted,
        p.winner_price,
        c.supplier_inn                                AS winner_inn,
        CASE
            WHEN pr.nmck IS NULL OR pr.nmck <= 0 THEN NULL
            WHEN p.winner_price IS NULL THEN NULL
            -- Неопределённый объём: предложение измеряется в суммах цен
            -- за единицу и с НМЦК несопоставимо.
            WHEN pr.quantity_undefined THEN NULL
            -- Цена выше НМЦК в обычной процедуре — аномалия, в медиану
            -- не берём, но ниже считаем такие отдельной колонкой.
            WHEN p.winner_price > pr.nmck THEN NULL
            ELSE (pr.nmck - p.winner_price) / pr.nmck
        END                                           AS price_drop,
        CASE
            WHEN pr.nmck IS NULL OR pr.nmck <= 0 OR p.winner_price IS NULL THEN FALSE
            WHEN pr.quantity_undefined THEN FALSE
            WHEN p.winner_price > pr.nmck THEN TRUE
            ELSE FALSE
        END                                           AS price_above_nmck
    FROM eis.procedure pr
    LEFT JOIN eis.protocol p ON p.purchase_number = pr.purchase_number
    LEFT JOIN eis.contract c ON c.purchase_number = pr.purchase_number
    WHERE pr.okpd2_primary IS NOT NULL
      AND pr.nmck IS NOT NULL
),
sliced AS (
    SELECT
        lvl                                           AS okpd2_level,
        eis.okpd2_prefix(b.okpd2_primary, lvl)        AS okpd2,
        b.region,
        CASE
            WHEN b.nmck <  500000 THEN '0-500k'
            WHEN b.nmck < 1000000 THEN '500k-1m'
            WHEN b.nmck < 3000000 THEN '1m-3m'
            ELSE                       '3m-5m'
        END                                           AS price_bucket,
        b.purchase_number,
        b.customer_inn,
        b.nmck,
        b.published_at,
        b.bids_submitted,
        b.winner_inn,
        b.price_drop,
        b.price_above_nmck,
        b.quantity_undefined
    FROM base b
    CROSS JOIN LATERAL (VALUES (2), (4), (6)) AS levels(lvl)
)
SELECT
    okpd2_level,
    okpd2,
    region,
    price_bucket,
    count(*)                                              AS procedures_count,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY bids_submitted)
        FILTER (WHERE bids_submitted IS NOT NULL)         AS median_bids,
    count(*) FILTER (WHERE bids_submitted = 1)::numeric
        / nullif(count(*) FILTER (WHERE bids_submitted IS NOT NULL), 0)
                                                          AS share_single_bid,
    count(*) FILTER (WHERE bids_submitted = 0)::numeric
        / nullif(count(*) FILTER (WHERE bids_submitted IS NOT NULL), 0)
                                                          AS share_zero_bid,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY price_drop)
        FILTER (WHERE price_drop IS NOT NULL)             AS median_drop,
    percentile_cont(0.9) WITHIN GROUP (ORDER BY price_drop)
        FILTER (WHERE price_drop IS NOT NULL)             AS p90_drop,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY nmck)     AS median_nmck,
    count(DISTINCT winner_inn)                            AS unique_winners,
    count(*) FILTER (WHERE winner_inn IS NOT NULL)        AS known_winners,
    -- На скольких процедурах основана медиана снижения и сколько
    -- отброшено: без этих чисел «снижение 0%» неотличимо от «данных нет».
    count(*) FILTER (WHERE price_drop IS NOT NULL)        AS comparable_prices,
    count(*) FILTER (WHERE quantity_undefined)            AS undefined_volume_count,
    count(*) FILTER (WHERE price_above_nmck)              AS price_above_nmck_count,
    array_agg(winner_inn) FILTER (WHERE winner_inn IS NOT NULL) AS winner_inns,
    array_agg(customer_inn) FILTER (WHERE customer_inn IS NOT NULL) AS customer_inns,
    jsonb_object_agg(month, cnt) FILTER (WHERE month IS NOT NULL) AS monthly_trend,
    min(published_at)                                     AS first_seen,
    max(published_at)                                     AS last_seen
FROM (
    SELECT s.*,
           to_char(date_trunc('month', s.published_at), 'YYYY-MM') AS month,
           count(*) OVER (PARTITION BY s.okpd2_level, s.okpd2, s.region,
                          s.price_bucket, date_trunc('month', s.published_at)) AS cnt
    FROM sliced s
) t
GROUP BY okpd2_level, okpd2, region, price_bucket;
"""


def upgrade() -> None:
    op.add_column('procedure', sa.Column('quantity_undefined', sa.Boolean(),
                                         nullable=True), schema=SCHEMA)
    op.execute("DROP MATERIALIZED VIEW IF EXISTS eis.niche_metrics")
    op.execute(VIEW)
    op.execute("CREATE UNIQUE INDEX ix_eis_niche_metrics_key "
               "ON eis.niche_metrics (okpd2_level, okpd2, region, price_bucket)")
    op.execute("CREATE INDEX ix_eis_niche_metrics_count "
               "ON eis.niche_metrics (procedures_count DESC)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS eis.niche_metrics")
    op.drop_column('procedure', 'quantity_undefined', schema=SCHEMA)
