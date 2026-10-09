-- =========================================================================================
-- E2 · supplier spend aggregation (easy)                    final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: a spend cube over purchase-order lines, one bare SELECT. PostgreSQL 17.
--   * GROUP BY a column and an expression; HAVING ................... D-2  influence / post filter
--   * COUNT(*), COUNT(DISTINCT), SUM over COALESCE, AVG, MAX ........ aggregated
--   * FILTER (WHERE) and SUM(CASE) .................................. D-10 values
--   * percentile_cont WITHIN GROUP, string_agg ORDER BY, bool_or ..... aggregated
--   * ROLLUP + GROUPING() read only by a scalar subquery ............ D-50, D-12
--   * a window over an aggregate (share, rank) ...................... D-4, D-46 influence
--   * WHERE over an aggregated CTE column; ORDER BY ... LIMIT ........ D-6 post, D-9
--   * a literal-only column ......................................... nothing
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
lines AS (
    SELECT h.vendor_id, h.currency_code, h.created_at,
           l.po_header_id, l.category_id, l.quantity, l.unit_price, l.amount,
           l.purchase_basis, l.line_status
    FROM s2p.po_header AS h
    JOIN s2p.po_line   AS l ON l.po_header_id = h.po_header_id
    WHERE h.document_status IN ('APPROVED', 'CLOSED')
),
vendor_year AS (
    SELECT ln.vendor_id,
           EXTRACT(YEAR FROM ln.created_at)::int                            AS spend_year,
           COUNT(*)                                                          AS line_count,
           COUNT(DISTINCT ln.category_id)                                    AS category_count,
           SUM(COALESCE(ln.amount, ln.quantity * ln.unit_price))             AS total_spend,
           SUM(ln.amount) FILTER (WHERE ln.purchase_basis = 'QUANTITY')      AS qty_spend,
           SUM(CASE WHEN ln.purchase_basis = 'FIXED PRICE' THEN ln.amount ELSE 0 END) AS fixed_spend,
           COUNT(*) FILTER (WHERE ln.unit_price > 1000)                      AS premium_lines,
           AVG(ln.unit_price)                                                AS avg_unit_price,
           MAX(ln.created_at)                                                AS last_po_ts,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ln.unit_price)        AS median_unit_price,
           string_agg(DISTINCT ln.currency_code, ',' ORDER BY ln.currency_code) AS currencies,
           bool_or(ln.line_status = 'OPEN')                                  AS any_open
    FROM lines AS ln
    GROUP BY ln.vendor_id, EXTRACT(YEAR FROM ln.created_at)
    HAVING SUM(ln.quantity) > 0
),
category_rollup AS (
    SELECT c.parent_category_id,
           pl.category_id,
           GROUPING(c.parent_category_id, pl.category_id) AS grouping_level,
           SUM(pl.amount)                                  AS category_spend,
           COUNT(DISTINCT pl.po_header_id)                 AS po_count
    FROM s2p.po_line       AS pl
    JOIN s2p.item_category AS c ON c.category_id = pl.category_id
    GROUP BY ROLLUP (c.parent_category_id, pl.category_id)
)
SELECT
    s.vendor_name                                                     AS supplier_name,
    vy.spend_year,
    vy.line_count,
    vy.category_count,
    vy.total_spend,
    vy.qty_spend,
    vy.fixed_spend,
    vy.premium_lines,
    ROUND(vy.avg_unit_price, 2)                                       AS avg_unit_price,
    vy.last_po_ts,
    vy.median_unit_price,
    vy.currencies,
    vy.any_open,
    vy.total_spend / NULLIF(SUM(vy.total_spend) OVER (PARTITION BY vy.spend_year), 0) AS share_of_year,
    RANK() OVER (PARTITION BY vy.spend_year ORDER BY vy.total_spend DESC)             AS spend_rank,
    (SELECT cr.category_spend
     FROM category_rollup AS cr
     WHERE cr.grouping_level = 3)                                     AS grand_category_spend,
    'SPEND'::text                                                     AS metric_family
FROM vendor_year AS vy
JOIN s2p.supplier AS s ON s.vendor_id = vy.vendor_id
WHERE s.status = 'ACTIVE'
  AND vy.total_spend > 1000
ORDER BY vy.spend_year DESC, vy.total_spend DESC
LIMIT 100;
