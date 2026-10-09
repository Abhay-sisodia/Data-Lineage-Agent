-- =========================================================================================
-- M3 · hierarchies, islands and sessions (medium)           final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: shipment lines placed in three hierarchies (organisation, customer, bill of
-- material) plus gap-and-island and session analytics, one bare SELECT. PostgreSQL 17.
--   * WITH RECURSIVE with a column list, SEARCH DEPTH FIRST and CYCLE ....... restated (TT-5)
--   * recursion counters (lvl, hop, bom_level): no base source ............ B2 S11
--   * a recursive arm guarded by NOT x = ANY (path) ........................ filter
--   * the CYCLE clause's own guard on the recursive arm .................... filter (reading)
--   * a recursive BOM explosion, aggregated and LEFT JOINed ................ D-45 aggregated
--   * gap-and-island: ROW_NUMBER inside a derived table over DISTINCT,
--     grouped twice, LEFT JOINed ........................................... D-2 influence
--   * sessionisation: LAG inside CASE, then a running SUM over it .......... D-4, influence carried
--   * a root column carried unchanged through the recursive arm ............ B2-10
--   * a LEFT join to the base table that a recursive CTE resolves to ....... values
--   * a per-organisation aggregate and a per-customer aggregate over a
--     window output, both LEFT JOINed ....................................... D-2, influence carried
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH RECURSIVE
org_tree (org_id, parent_org_id, org_name, root_name, lvl, path_txt) AS (
    SELECT o.org_id, o.parent_org_id, o.org_name, o.org_name, 1, o.org_name::text
    FROM crm.organization AS o
    WHERE o.parent_org_id IS NULL
      AND o.is_active
    UNION ALL
    SELECT c.org_id, c.parent_org_id, c.org_name, t.root_name, t.lvl + 1,
           t.path_txt || ' > ' || c.org_name
    FROM crm.organization AS c
    JOIN org_tree AS t ON c.parent_org_id = t.org_id
    WHERE t.lvl < 20
) SEARCH DEPTH FIRST BY org_id SET dfs_order
  CYCLE org_id SET is_cycle USING cycle_path,
cust_rollup (customer_id, ultimate_parent_id, hop, chain) AS (
    SELECT c.customer_id, c.customer_id, 0, ARRAY[c.customer_id]
    FROM crm.customer AS c
    WHERE c.parent_customer_id IS NULL
    UNION ALL
    SELECT ch.customer_id, r.ultimate_parent_id, r.hop + 1, r.chain || ch.customer_id
    FROM crm.customer AS ch
    JOIN cust_rollup AS r ON ch.parent_customer_id = r.customer_id
    WHERE NOT ch.customer_id = ANY (r.chain)
      AND r.hop < 15
),
bom_explode (top_item_id, component_item_id, ext_qty, bom_level) AS (
    SELECT b.parent_item_id, b.component_item_id, b.qty_per, 1
    FROM lgx.bill_of_material AS b
    WHERE b.bom_status = 'ACTIVE'
    UNION ALL
    SELECT be.top_item_id,
           b2.component_item_id,
           (be.ext_qty * b2.qty_per * (1 + COALESCE(b2.scrap_pct, 0) / 100.0))::numeric(18,6),
           be.bom_level + 1
    FROM bom_explode AS be
    JOIN lgx.bill_of_material AS b2 ON b2.parent_item_id = be.component_item_id
    WHERE b2.bom_status = 'ACTIVE'
      AND be.bom_level < 10
),
bom_summary AS (
    SELECT be.top_item_id,
           COUNT(DISTINCT be.component_item_id) AS component_count,
           SUM(be.ext_qty)                      AS total_component_qty,
           MAX(be.bom_level)                    AS bom_depth
    FROM bom_explode AS be
    GROUP BY be.top_item_id
),
lines AS (
    SELECT l.shipment_line_id, h.customer_id, h.ship_from_org_id, l.item_id,
           l.ship_date, l.shipped_qty
    FROM lgx.shipment_line   AS l
    JOIN lgx.shipment_header AS h ON h.shipment_id = l.shipment_id
    WHERE l.ship_date IS NOT NULL
),
islands AS (
    SELECT g.customer_id,
           g.grp_key,
           MIN(g.ship_date) AS island_start,
           MAX(g.ship_date) AS island_end,
           COUNT(*)         AS island_days
    FROM (
        SELECT d.customer_id,
               d.ship_date,
               d.ship_date - (ROW_NUMBER() OVER (PARTITION BY d.customer_id
                                                 ORDER BY d.ship_date))::int AS grp_key
        FROM (SELECT DISTINCT ln.customer_id, ln.ship_date FROM lines AS ln) AS d
    ) AS g
    GROUP BY g.customer_id, g.grp_key
    HAVING COUNT(*) >= 2
),
island_stats AS (
    SELECT i.customer_id,
           COUNT(*)           AS island_count,
           MAX(i.island_days) AS longest_island
    FROM islands AS i
    GROUP BY i.customer_id
),
sessions AS (
    SELECT s1.shipment_line_id,
           SUM(s1.is_new) OVER (PARTITION BY s1.customer_id
                                ORDER BY s1.ship_date, s1.shipment_line_id
                                ROWS UNBOUNDED PRECEDING)          AS session_no
    FROM (
        SELECT ln.shipment_line_id,
               ln.customer_id,
               ln.ship_date,
               CASE
                   WHEN LAG(ln.ship_date) OVER (PARTITION BY ln.customer_id ORDER BY ln.ship_date) IS NULL
                       THEN 1
                   WHEN ln.ship_date - LAG(ln.ship_date) OVER (PARTITION BY ln.customer_id
                                                               ORDER BY ln.ship_date) > 7
                       THEN 1
                   ELSE 0
               END AS is_new
        FROM lines AS ln
    ) AS s1
),
org_wh AS (
    SELECT w.org_id,
           COUNT(*)                                             AS warehouse_count,
           string_agg(w.warehouse_code, ',' ORDER BY w.warehouse_code) AS warehouse_codes
    FROM lgx.warehouse AS w
    GROUP BY w.org_id
),
session_stats AS (
    SELECT ln2.customer_id,
           MAX(ss.session_no) AS session_count
    FROM sessions AS ss
    JOIN lines    AS ln2 ON ln2.shipment_line_id = ss.shipment_line_id
    GROUP BY ln2.customer_id
)
SELECT
    l.shipment_line_id,
    l.customer_id,
    cr.ultimate_parent_id,
    up.customer_name                                   AS ultimate_parent_name,
    cardinality(cr.chain)                              AS hierarchy_depth,
    array_to_string(cr.chain, '>')                     AS hierarchy_path,
    ot.org_name                                        AS ship_from_org,
    ot.root_name                                       AS ship_from_root,
    ot.path_txt                                        AS org_path,
    ot.lvl                                             AS org_level,
    ot.dfs_order                                       AS org_dfs_order,
    ot.is_cycle                                        AS org_is_cycle,
    s.session_no,
    l.ship_date,
    l.shipped_qty,
    COUNT(*) OVER (PARTITION BY l.customer_id)         AS customer_lines,
    bs.component_count,
    bs.total_component_qty,
    bs.bom_depth,
    ist.island_count,
    ist.longest_island,
    ow.warehouse_count,
    ow.warehouse_codes,
    sst.session_count
FROM lines AS l
JOIN cust_rollup  AS cr  ON cr.customer_id = l.customer_id
JOIN org_tree     AS ot  ON ot.org_id = l.ship_from_org_id
JOIN sessions     AS s   ON s.shipment_line_id = l.shipment_line_id
LEFT JOIN crm.customer AS up  ON up.customer_id = cr.ultimate_parent_id
LEFT JOIN bom_summary  AS bs  ON bs.top_item_id = l.item_id
LEFT JOIN island_stats AS ist ON ist.customer_id = l.customer_id
LEFT JOIN org_wh       AS ow  ON ow.org_id = ot.org_id
LEFT JOIN session_stats AS sst ON sst.customer_id = l.customer_id
WHERE l.shipped_qty > 0;
