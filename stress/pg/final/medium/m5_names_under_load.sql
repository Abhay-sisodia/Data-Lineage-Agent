-- =========================================================================================
-- M5 · names under load (medium)                            final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: a shipment board whose every name is a resolution question, one bare SELECT.
-- PostgreSQL 17.
--   * a quoted CTE name that is a keyword ("Order"), quoted mixed-case and keyword
--     column aliases ("Customer_Id", "select"), a keyword output alias ("from")
--   * unquoted mixed case folding: Lines / lines, LN / ln, L4 / l4, SHIPPED_QTY
--   * `x.*` carried through four CTE levels, with WHERE on derived and carried columns
--   * the same alias (x) for different relations in different scopes, including a
--     scalar subquery nested in another's WHERE ............................. D-12, D-45
--   * a CTE named like a base table (customer), shadowed again by a nested WITH
--     inside another CTE ................................................... forbid the leak
--   * JOIN USING whose merged column is then used unqualified
--   * an unqualified column that only one of two relations has (country_code)
--   * a table referenced by its bare name, no alias (lgx.warehouse -> warehouse.x)
--   * system columns: ctid as a DISTINCT ON tie-breaker, xmin projected ..... TT-1 (nothing)
--   * columns that share a name across many tables (status, created_at, customer_id)
--   * a UNION ALL whose arms rename different columns to one name, aggregated
--   * a CTE named `status` with a column named `status`; a quoted output with spaces
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
"Order" AS (
    SELECT DISTINCT ON (h.shipment_id)
           h.shipment_id      AS "id",
           h.customer_id      AS "Customer_Id",
           h.status_code      AS "select",
           h.ship_from_org_id,
           h.created_at,
           h.xmin             AS row_xmin
    FROM lgx.shipment_header AS h
    WHERE h.is_deleted IS NOT TRUE
    ORDER BY h.shipment_id, h.updated_at DESC, h.ctid DESC
),
Lines AS (
    SELECT *
    FROM lgx.shipment_line AS LN
    WHERE ln.line_status <> 'VOID'
),
lvl1 AS (
    SELECT l.*
    FROM lines AS l
),
lvl2 AS (
    SELECT x.*, x.shipped_qty * x.unit_selling_price AS ext
    FROM lvl1 AS x
),
lvl3 AS (
    SELECT *
    FROM lvl2
    WHERE ext > 0
),
lvl4 AS (
    SELECT x.*
    FROM lvl3 AS x
    WHERE x.promise_date IS NOT NULL
),
customer AS (
    SELECT c.customer_id, c.customer_name AS name, c.status, c.segment, c.created_at
    FROM crm.customer AS c
),
nested AS (
    WITH customer AS (
        SELECT ct.customer_id, ct.email AS name, ct.role AS status
        FROM crm.contact AS ct
        WHERE ct.is_primary
    )
    SELECT customer.customer_id,
           customer.name   AS primary_email,
           customer.status AS contact_role
    FROM customer
),
site AS (
    SELECT customer_id, s.site_code, cu.segment, cu.name AS customer_name
    FROM crm.customer_site AS s
    JOIN customer AS cu USING (customer_id)
    WHERE country_code = 'US'
),
events AS (
    SELECT pe.shipment_id, pe.event_ts AS ts, 'PKG' AS src
    FROM lgx.package_event AS pe
    UNION ALL
    SELECT a.entity_id, a.created_at, 'ACT'
    FROM evt.activity AS a
    WHERE a.kind = 'SHIPMENT'
),
last_touch AS (
    SELECT e.shipment_id, MAX(e.ts) AS last_touch_ts, COUNT(DISTINCT e.src) AS touch_sources
    FROM events AS e
    GROUP BY e.shipment_id
),
status AS (
    SELECT ds.status_code AS status, ds.status_name
    FROM gold.dim_status AS ds
    WHERE ds.datasource_num_id = 100
),
regions AS (
    SELECT warehouse.org_id, warehouse.region_code, warehouse.warehouse_code
    FROM lgx.warehouse
)
SELECT
    o."id"                                             AS shipment_id,
    o."Customer_Id"                                    AS customer_id,
    o."select"                                         AS "from",
    o.row_xmin,
    cu.name                                            AS customer_name,
    cu.status                                          AS customer_status,
    cu.created_at                                      AS customer_created_at,
    n.primary_email,
    n.contact_role,
    st.site_code                                       AS "Ship To Site",
    st.segment                                         AS site_segment,
    rg.region_code,
    L4.shipment_line_id,
    l4.ext,
    L4.SHIPPED_QTY                                     AS shipped_qty,
    l4.last_update_ts                                  AS line_updated_at,
    (SELECT x.carrier_name
     FROM lgx.carrier AS x
     WHERE x.carrier_code = (SELECT x.carrier_code
                             FROM lgx.shipment_header AS x
                             WHERE x.shipment_id = o."id"))    AS carrier_name,
    lt.last_touch_ts,
    lt.touch_sources,
    status.status_name
FROM "Order" AS o
JOIN lvl4 AS L4          ON l4.shipment_id = o."id"
LEFT JOIN customer AS cu ON cu.customer_id = o."Customer_Id"
LEFT JOIN nested   AS n  ON n.customer_id = o."Customer_Id"
LEFT JOIN site     AS st ON st.customer_id = o."Customer_Id"
LEFT JOIN regions  AS rg ON rg.org_id = o.ship_from_org_id
LEFT JOIN last_touch AS lt ON lt.shipment_id = o."id"
LEFT JOIN status         ON status.status = o."select"
WHERE o."select" <> 'VOID'
ORDER BY 1, 13;
