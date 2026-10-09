-- =========================================================================================
-- E5 · entity activity windows (easy)                       final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: running and ranked activity per entity, one bare SELECT. PostgreSQL 17.
--   * named windows, one inheriting another (w AS (w_base ORDER BY)) .. D-4 influence
--   * ROWS / RANGE INTERVAL frames ................................... aggregated + influence
--   * LAG / LEAD / FIRST_VALUE ........................................ D-3 derived
--   * ROW_NUMBER / DENSE_RANK / NTILE / PERCENT_RANK / COUNT(*) OVER .. influence only
--   * windows over an aggregate (GROUP BY behind the ordering) ........ D-46
--   * influence carried into a downstream expression ................. influence
--   * a ranking window then cut (`<= 10`) ............................ D-9 filters
--   * a LEFT join with a one-relation ON condition .................... D-48 conditional
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
acts AS (
    SELECT a.activity_id, a.entity_id, a.kind, a.amount, a.region_code,
           a.sequence_no, a.created_at
    FROM evt.activity AS a
    WHERE a.created_at >= TIMESTAMPTZ '2024-01-01 00:00:00+00'
),
running AS (
    SELECT ac.*,
           SUM(ac.amount)    OVER w_entity_ordered                              AS running_amount,
           SUM(ac.amount)    OVER (w_entity ORDER BY ac.created_at
                                   RANGE BETWEEN INTERVAL '30 days' PRECEDING
                                             AND CURRENT ROW)                   AS amount_30d,
           AVG(ac.amount)    OVER (w_entity_ordered ROWS BETWEEN 2 PRECEDING
                                                         AND CURRENT ROW)       AS moving_avg_3,
           LAG(ac.amount)    OVER w_entity_ordered                              AS prev_amount,
           LEAD(ac.created_at, 1) OVER w_entity_ordered                         AS next_activity_ts,
           ROW_NUMBER()      OVER w_entity_ordered                              AS activity_seq,
           FIRST_VALUE(ac.kind) OVER (w_entity ORDER BY ac.created_at
                                      ROWS BETWEEN UNBOUNDED PRECEDING
                                               AND UNBOUNDED FOLLOWING)         AS first_kind,
           COUNT(*)          OVER (PARTITION BY ac.region_code)                 AS region_events,
           DENSE_RANK()      OVER (PARTITION BY ac.region_code
                                   ORDER BY ac.amount DESC NULLS LAST)          AS region_amount_rank
    FROM acts AS ac
    WINDOW w_entity         AS (PARTITION BY ac.entity_id),
           w_entity_ordered AS (w_entity ORDER BY ac.created_at, ac.sequence_no)
),
entity_totals AS (
    SELECT ac.entity_id, ac.region_code,
           SUM(ac.amount) AS total_amount,
           COUNT(*)       AS activity_count
    FROM acts AS ac
    GROUP BY ac.entity_id, ac.region_code
),
ranked_entities AS (
    SELECT et.*,
           RANK() OVER (PARTITION BY et.region_code ORDER BY et.total_amount DESC) AS entity_rank_in_region,
           et.total_amount
               / NULLIF(SUM(et.total_amount) OVER (PARTITION BY et.region_code), 0) AS share_in_region,
           NTILE(4) OVER (ORDER BY et.activity_count)                             AS activity_quartile
    FROM entity_totals AS et
)
SELECT
    r.activity_id,
    r.entity_id,
    r.kind,
    r.amount,
    r.running_amount,
    r.amount_30d,
    r.moving_avg_3,
    r.prev_amount,
    r.amount - COALESCE(r.prev_amount, 0)                AS delta_amount,
    r.next_activity_ts,
    r.activity_seq,
    r.first_kind,
    r.region_events,
    r.region_amount_rank,
    re.total_amount                                      AS entity_total,
    re.entity_rank_in_region,
    re.share_in_region,
    re.activity_quartile,
    e.display_name,
    PERCENT_RANK() OVER (ORDER BY r.amount)              AS amount_pct_rank
FROM running AS r
JOIN ranked_entities AS re
  ON re.entity_id = r.entity_id
 AND re.region_code = r.region_code
LEFT JOIN evt.entity_event AS e
  ON e.entity_id = r.entity_id
 AND e.status = 'ACTIVE'
WHERE r.activity_seq <= 10
ORDER BY r.entity_id, r.activity_seq;
