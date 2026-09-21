"""Витрина метрик ниш: eis.niche_metrics.

Срез = код ОКПД2 на уровне 2/4/6 × регион × ценовая корзина. По каждому
считаются те самые цифры, ради которых затевался модуль: сколько бывает
участников, как роняют цену и не схвачена ли ниша одним поставщиком.

Материализованное представление, а не таблица: данные полностью
выводятся из eis.procedure/protocol/contract, и пересчёт раз в сутки
после загрузки дешевле, чем поддержание в согласованном состоянии на
каждой вставке.

Индекс привлекательности здесь НЕ считается — он в
tender_sniper/niche/metrics.py, потому что по ТЗ веса и константы
должны лежать в конфиге, а не в коде. Витрина отдаёт сырые метрики,
взвешивание — снаружи.

Revision ID: 20260921_niche_mv
Revises: 20260921_eis
Create Date: 2026-09-21

"""
from alembic import op


revision = '20260921_niche_mv'
down_revision = '20260921_eis'
branch_labels = None
depends_on = None


# Уровень ОКПД2 → сколько групп кода оставить: 2 знака это «21»,
# 4 знака «21.20», 6 знаков «21.20.10».
OKPD2_PREFIX = """
CREATE OR REPLACE FUNCTION eis.okpd2_prefix(code text, lvl int)
RETURNS text AS $$
  SELECT CASE
    WHEN code IS NULL THEN NULL
    ELSE array_to_string((string_to_array(code, '.'))[1:lvl / 2], '.')
  END
$$ LANGUAGE sql IMMUTABLE;
"""

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
        p.bids_submitted,
        p.winner_price,
        -- Победитель известен только из реестра контрактов: в протоколе
        -- участник обезличен номером заявки.
        c.supplier_inn                                AS winner_inn,
        CASE
            WHEN pr.nmck IS NULL OR pr.nmck <= 0 OR p.winner_price IS NULL THEN NULL
            ELSE (pr.nmck - p.winner_price) / pr.nmck
        END                                           AS price_drop
    FROM eis.procedure pr
    LEFT JOIN eis.protocol p ON p.purchase_number = pr.purchase_number
    LEFT JOIN eis.contract c ON c.purchase_number = pr.purchase_number
    WHERE pr.okpd2_primary IS NOT NULL
      AND pr.nmck IS NOT NULL
),
-- Колонки перечислены поимённо, а не через b.*: со звёздочкой region
-- и прочие уже выбранные поля приезжают дважды, и PostgreSQL
-- отвечает «column reference is ambiguous».
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
        b.price_drop
    FROM base b
    CROSS JOIN LATERAL (VALUES (2), (4), (6)) AS levels(lvl)
)
SELECT
    okpd2_level,
    okpd2,
    region,
    price_bucket,
    count(*)                                              AS procedures_count,
    -- Медианы считаем только по процедурам, где протокол уже есть:
    -- иначе свежие закупки без результата занижали бы число заявок.
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
    -- Доли победителей для HHI и топ-3 считаются отдельным запросом,
    -- здесь собираем сырой набор ИНН: агрегировать долю доли внутри
    -- одного GROUP BY нельзя, а второй проход по срезу дороже, чем
    -- посчитать из массива при чтении.
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

# Уникальный индекс обязателен для REFRESH MATERIALIZED VIEW CONCURRENTLY:
# без него пересчёт блокирует чтение витрины.
UNIQUE_INDEX = """
CREATE UNIQUE INDEX ix_eis_niche_metrics_key
ON eis.niche_metrics (okpd2_level, okpd2, region, price_bucket);
"""


def upgrade() -> None:
    op.execute(OKPD2_PREFIX)
    op.execute(VIEW)
    op.execute(UNIQUE_INDEX)
    op.execute("CREATE INDEX ix_eis_niche_metrics_count "
               "ON eis.niche_metrics (procedures_count DESC)")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS eis.niche_metrics")
    op.execute("DROP FUNCTION IF EXISTS eis.okpd2_prefix(text, int)")
