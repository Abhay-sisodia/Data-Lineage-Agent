-- =========================================================================================
-- H5 · adversarial and metamorphic (hard)                   final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Part 1 - METAMORPHIC: 13 groups of statements that compute the same thing in different
-- spellings. Each spelling writes its own temp table with the same column names, so the
-- lineage of `pN_a` must equal `pN_b` (and `p4_c`) once the target table is set aside. The
-- conventions say each group is equivalent:
--   P1  a CTE vs the same query as a derived table
--   P2  an INNER join's one-relation condition in ON vs in WHERE .......... D-48
--   P3  COUNT(*) / SUM(x) FILTER (WHERE c) vs SUM(CASE WHEN c ...) ........ D-10
--   P4  IN vs = ANY vs correlated EXISTS ................................... D-52
--   P5  DISTINCT ON vs ROW_NUMBER() ... = 1 ................................ D-9
--   P6  COALESCE(a, b) vs CASE WHEN a IS NOT NULL THEN a ELSE b END ....... D-19
--   P7  GROUP BY 1 / ORDER BY 2 vs names and the expression, with LIMIT .... D-9, D-6
--   P8  JOIN USING vs JOIN ON .............................................. D-5
--   P9  an inline window vs an inherited named window ...................... D-4
--   P10 a SELECT-list scalar subquery vs a one-row LEFT LATERAL ............ D-12, D-14
--   P11 UNION ALL of two filtered arms vs one WHERE with OR
--   P12 a CTE read twice vs the same derived table written twice
--   P13 a comma join with the key equality in WHERE vs JOIN ON ............. D-11
--   P14 NOT IN vs <> ALL vs correlated NOT EXISTS .......................... D-52
--   P15 HAVING vs a WHERE over the aggregated derived table ................ D-6 post
--   P16 a simple CASE vs the searched CASE ................................. D-19
--   P17 BETWEEN vs >= AND <=
--   P18 EXTRACT(YEAR FROM x) vs date_part('year', x)
--   P19 || vs concat()
--   P20 x::numeric vs CAST(x AS numeric)
-- Part 2 - ADVERSARIAL: SQL inside string, escape-string and dollar-quoted literals and
-- inside comments (with `;` and `$$`); CTEs named like base tables, read beside the
-- qualified base tables; keyword and quoted output aliases; an ORDER BY name that resolves
-- to an OUTPUT column and a GROUP BY name that resolves to an INPUT column; literal-only
-- columns. Nothing in a literal or comment may produce an edge or split a statement.
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================

-- P1 · CTE ---------------------------------------------------------------------------------
CREATE TEMP TABLE p1_a AS
WITH spend AS (
    SELECT h.vendor_id, SUM(l.amount) AS total
    FROM s2p.po_header AS h
    JOIN s2p.po_line   AS l ON l.po_header_id = h.po_header_id
    WHERE h.document_status = 'APPROVED'
    GROUP BY h.vendor_id
    HAVING SUM(l.quantity) > 0
)
SELECT s.vendor_name, sp.total
FROM spend AS sp
JOIN s2p.supplier AS s ON s.vendor_id = sp.vendor_id;

-- P1 · derived table ------------------------------------------------------------------------
CREATE TEMP TABLE p1_b AS
SELECT s.vendor_name, sp.total
FROM (
    SELECT h.vendor_id, SUM(l.amount) AS total
    FROM s2p.po_header AS h
    JOIN s2p.po_line   AS l ON l.po_header_id = h.po_header_id
    WHERE h.document_status = 'APPROVED'
    GROUP BY h.vendor_id
    HAVING SUM(l.quantity) > 0
) AS sp
JOIN s2p.supplier AS s ON s.vendor_id = sp.vendor_id;

-- P2 · condition in ON ------------------------------------------------------------------------
CREATE TEMP TABLE p2_a AS
SELECT l.shipment_line_id, h.carrier_code
FROM lgx.shipment_line   AS l
JOIN lgx.shipment_header AS h ON h.shipment_id = l.shipment_id
                             AND h.status_code = 'SHIPPED';

-- P2 · condition in WHERE ---------------------------------------------------------------------
CREATE TEMP TABLE p2_b AS
SELECT l.shipment_line_id, h.carrier_code
FROM lgx.shipment_line   AS l
JOIN lgx.shipment_header AS h ON h.shipment_id = l.shipment_id
WHERE h.status_code = 'SHIPPED';

-- P3 · FILTER ---------------------------------------------------------------------------------
CREATE TEMP TABLE p3_a AS
SELECT a.entity_id,
       COUNT(*) FILTER (WHERE a.kind = 'X')       AS x_count,
       SUM(a.amount) FILTER (WHERE a.kind = 'X')  AS x_amount
FROM evt.activity AS a
GROUP BY a.entity_id;

-- P3 · SUM(CASE) ------------------------------------------------------------------------------
CREATE TEMP TABLE p3_b AS
SELECT a.entity_id,
       SUM(CASE WHEN a.kind = 'X' THEN 1 ELSE 0 END)   AS x_count,
       SUM(CASE WHEN a.kind = 'X' THEN a.amount END)   AS x_amount
FROM evt.activity AS a
GROUP BY a.entity_id;

-- P4 · IN -----------------------------------------------------------------------------------
CREATE TEMP TABLE p4_a AS
SELECT c.customer_id, c.customer_name
FROM crm.customer AS c
WHERE c.customer_id IN (SELECT ct.customer_id FROM crm.contact AS ct WHERE ct.is_primary);

-- P4 · = ANY --------------------------------------------------------------------------------
CREATE TEMP TABLE p4_b AS
SELECT c.customer_id, c.customer_name
FROM crm.customer AS c
WHERE c.customer_id = ANY (SELECT ct.customer_id FROM crm.contact AS ct WHERE ct.is_primary);

-- P4 · EXISTS -------------------------------------------------------------------------------
CREATE TEMP TABLE p4_c AS
SELECT c.customer_id, c.customer_name
FROM crm.customer AS c
WHERE EXISTS (SELECT 1
              FROM crm.contact AS ct
              WHERE ct.customer_id = c.customer_id
                AND ct.is_primary);

-- P5 · DISTINCT ON ---------------------------------------------------------------------------
CREATE TEMP TABLE p5_a AS
SELECT DISTINCT ON (e.entity_id) e.entity_id, e.status
FROM evt.entity_event AS e
ORDER BY e.entity_id, e.created_at DESC;

-- P5 · ROW_NUMBER() = 1 ----------------------------------------------------------------------
CREATE TEMP TABLE p5_b AS
SELECT q.entity_id, q.status
FROM (
    SELECT e.entity_id, e.status,
           ROW_NUMBER() OVER (PARTITION BY e.entity_id ORDER BY e.created_at DESC) AS rn
    FROM evt.entity_event AS e
) AS q
WHERE q.rn = 1;

-- P6 · COALESCE -------------------------------------------------------------------------------
CREATE TEMP TABLE p6_a AS
SELECT s.site_id, COALESCE(s.time_zone_name, c.country_code) AS tz
FROM crm.customer_site AS s
JOIN crm.customer      AS c ON c.customer_id = s.customer_id;

-- P6 · CASE -----------------------------------------------------------------------------------
CREATE TEMP TABLE p6_b AS
SELECT s.site_id,
       CASE WHEN s.time_zone_name IS NOT NULL THEN s.time_zone_name ELSE c.country_code END AS tz
FROM crm.customer_site AS s
JOIN crm.customer      AS c ON c.customer_id = s.customer_id;

-- P7 · positions ------------------------------------------------------------------------------
CREATE TEMP TABLE p7_a AS
SELECT w.region_code, COUNT(DISTINCT w.org_id) AS orgs
FROM lgx.warehouse AS w
GROUP BY 1
ORDER BY 2 DESC
LIMIT 5;

-- P7 · names ----------------------------------------------------------------------------------
CREATE TEMP TABLE p7_b AS
SELECT w.region_code, COUNT(DISTINCT w.org_id) AS orgs
FROM lgx.warehouse AS w
GROUP BY w.region_code
ORDER BY COUNT(DISTINCT w.org_id) DESC
LIMIT 5;

-- P8 · USING ----------------------------------------------------------------------------------
CREATE TEMP TABLE p8_a AS
SELECT carrier_code, cs.service_level, c.carrier_name
FROM lgx.carrier_service AS cs
JOIN lgx.carrier         AS c USING (carrier_code);

-- P8 · ON -------------------------------------------------------------------------------------
CREATE TEMP TABLE p8_b AS
SELECT cs.carrier_code, cs.service_level, c.carrier_name
FROM lgx.carrier_service AS cs
JOIN lgx.carrier         AS c ON c.carrier_code = cs.carrier_code;

-- P9 · inline window --------------------------------------------------------------------------
CREATE TEMP TABLE p9_a AS
SELECT a.activity_id,
       SUM(a.amount) OVER (PARTITION BY a.entity_id ORDER BY a.created_at) AS running
FROM evt.activity AS a;

-- P9 · named, inherited window ----------------------------------------------------------------
CREATE TEMP TABLE p9_b AS
SELECT a.activity_id,
       SUM(a.amount) OVER w2 AS running
FROM evt.activity AS a
WINDOW w1 AS (PARTITION BY a.entity_id),
       w2 AS (w1 ORDER BY a.created_at);

-- P10 · scalar subquery -----------------------------------------------------------------------
CREATE TEMP TABLE p10_a AS
SELECT c.customer_id,
       (SELECT MAX(sh.created_at)
        FROM lgx.shipment_header AS sh
        WHERE sh.customer_id = c.customer_id) AS last_ship
FROM crm.customer AS c;

-- P10 · one-row LEFT LATERAL ------------------------------------------------------------------
CREATE TEMP TABLE p10_b AS
SELECT c.customer_id, x.last_ship
FROM crm.customer AS c
LEFT JOIN LATERAL (
    SELECT MAX(sh.created_at) AS last_ship
    FROM lgx.shipment_header AS sh
    WHERE sh.customer_id = c.customer_id
) AS x ON TRUE;

-- P11 · UNION ALL of two arms -----------------------------------------------------------------
CREATE TEMP TABLE p11_a AS
SELECT e.entity_id, e.amount FROM evt.entity_event AS e WHERE e.status = 'OPEN'
UNION ALL
SELECT e.entity_id, e.amount FROM evt.entity_event AS e WHERE e.amount > 1000;

-- P11 · OR ------------------------------------------------------------------------------------
CREATE TEMP TABLE p11_b AS
SELECT e.entity_id, e.amount
FROM evt.entity_event AS e
WHERE e.status = 'OPEN' OR e.amount > 1000;

-- P12 · a CTE read twice ----------------------------------------------------------------------
CREATE TEMP TABLE p12_a AS
WITH big AS (
    SELECT a.entity_id, a.amount FROM evt.activity AS a WHERE a.amount > 100
)
SELECT x.entity_id, x.amount AS amt_x, y.amount AS amt_y
FROM big AS x
JOIN big AS y ON y.entity_id = x.entity_id;

-- P12 · the derived table written twice -------------------------------------------------------
CREATE TEMP TABLE p12_b AS
SELECT x.entity_id, x.amount AS amt_x, y.amount AS amt_y
FROM (SELECT a.entity_id, a.amount FROM evt.activity AS a WHERE a.amount > 100) AS x
JOIN (SELECT a.entity_id, a.amount FROM evt.activity AS a WHERE a.amount > 100) AS y
  ON y.entity_id = x.entity_id;

-- P13 · comma join, key in WHERE --------------------------------------------------------------
CREATE TEMP TABLE p13_a AS
SELECT o.org_name, w.warehouse_code
FROM crm.organization AS o, lgx.warehouse AS w
WHERE w.org_id = o.org_id
  AND o.is_active;

-- P13 · JOIN ON -------------------------------------------------------------------------------
CREATE TEMP TABLE p13_b AS
SELECT o.org_name, w.warehouse_code
FROM crm.organization AS o
JOIN lgx.warehouse    AS w ON w.org_id = o.org_id
WHERE o.is_active;

-- P14 · NOT IN --------------------------------------------------------------------------------
CREATE TEMP TABLE p14_a AS
SELECT i.item_id, i.item_code
FROM lgx.item_master AS i
WHERE i.item_id NOT IN (SELECT b.component_item_id FROM lgx.bill_of_material AS b WHERE b.bom_status = 'OBSOLETE');

-- P14 · <> ALL --------------------------------------------------------------------------------
CREATE TEMP TABLE p14_b AS
SELECT i.item_id, i.item_code
FROM lgx.item_master AS i
WHERE i.item_id <> ALL (SELECT b.component_item_id FROM lgx.bill_of_material AS b WHERE b.bom_status = 'OBSOLETE');

-- P14 · NOT EXISTS ----------------------------------------------------------------------------
CREATE TEMP TABLE p14_c AS
SELECT i.item_id, i.item_code
FROM lgx.item_master AS i
WHERE NOT EXISTS (SELECT 1
                  FROM lgx.bill_of_material AS b
                  WHERE b.component_item_id = i.item_id
                    AND b.bom_status = 'OBSOLETE');

-- P15 · HAVING --------------------------------------------------------------------------------
CREATE TEMP TABLE p15_a AS
SELECT a.region_code, SUM(a.amount) AS total
FROM evt.activity AS a
GROUP BY a.region_code
HAVING SUM(a.amount) > 0;

-- P15 · WHERE over the aggregate --------------------------------------------------------------
CREATE TEMP TABLE p15_b AS
SELECT g.region_code, g.total
FROM (SELECT a.region_code, SUM(a.amount) AS total
      FROM evt.activity AS a
      GROUP BY a.region_code) AS g
WHERE g.total > 0;

-- P16 · simple CASE ---------------------------------------------------------------------------
CREATE TEMP TABLE p16_a AS
SELECT a.activity_id, CASE a.kind WHEN 'REFUND' THEN -a.amount ELSE a.amount END AS signed_amount
FROM evt.activity AS a;

-- P16 · searched CASE -------------------------------------------------------------------------
CREATE TEMP TABLE p16_b AS
SELECT a.activity_id, CASE WHEN a.kind = 'REFUND' THEN -a.amount ELSE a.amount END AS signed_amount
FROM evt.activity AS a;

-- P17 · BETWEEN -------------------------------------------------------------------------------
CREATE TEMP TABLE p17_a AS
SELECT m.entity_id, m.metric
FROM evt.metric AS m
WHERE m.score BETWEEN 0.4 AND 0.9;

-- P17 · >= AND <= -----------------------------------------------------------------------------
CREATE TEMP TABLE p17_b AS
SELECT m.entity_id, m.metric
FROM evt.metric AS m
WHERE m.score >= 0.4 AND m.score <= 0.9;

-- P18 · EXTRACT -------------------------------------------------------------------------------
CREATE TEMP TABLE p18_a AS
SELECT m.entity_id, EXTRACT(YEAR FROM m.event_date) AS event_year
FROM evt.metric AS m;

-- P18 · date_part -----------------------------------------------------------------------------
CREATE TEMP TABLE p18_b AS
SELECT m.entity_id, date_part('year', m.event_date) AS event_year
FROM evt.metric AS m;

-- P19 · || ------------------------------------------------------------------------------------
CREATE TEMP TABLE p19_a AS
SELECT c.contact_id, c.role || ':' || c.email AS label
FROM crm.contact AS c;

-- P19 · concat() ------------------------------------------------------------------------------
CREATE TEMP TABLE p19_b AS
SELECT c.contact_id, concat(c.role, ':', c.email) AS label
FROM crm.contact AS c;

-- P20 · :: ------------------------------------------------------------------------------------
CREATE TEMP TABLE p20_a AS
SELECT r.child_id, r.weight::numeric(12, 2) AS weight
FROM evt.relationship AS r;

-- P20 · CAST ----------------------------------------------------------------------------------
CREATE TEMP TABLE p20_b AS
SELECT r.child_id, CAST(r.weight AS numeric(12, 2)) AS weight
FROM evt.relationship AS r;

-- =========================================================================================
-- Part 2 · adversarial
-- =========================================================================================
-- T1 · literals and comments full of SQL; CTEs named like base tables
-- (this comment holds a ; and a $$ and the words SELECT secret FROM crm.customer)
WITH customer AS (
    SELECT ct.customer_id, ct.email AS customer_name, ct.role AS status
    FROM crm.contact AS ct
),
contact AS (
    SELECT f.entity_id AS customer_id, f.flag_code AS email
    FROM evt.flag AS f
)
SELECT
    cu.customer_id,
    cu.customer_name,
    k.customer_name                                          AS contact_email,
    ct.email                                                 AS primary_email,
    'SELECT c.customer_name FROM crm.customer AS c; --'      AS sql_in_string,
    E'it\'s ; INSERT INTO mart.load_audit SELECT 1'          AS sql_in_escape_string,
    $q$ DELETE FROM mart.fact_po_line; SELECT secret FROM crm.customer $q$ AS sql_in_dollar,
    /* SELECT credit_limit FROM crm.customer; */ cu.segment  AS "select",
    -- ; INSERT INTO mart.load_audit SELECT customer_id FROM crm.customer $$
    cu.status                                                AS "from",
    cu.country_code                                          AS "we""ird",
    NULL::text                                               AS reserved,
    now()                                                    AS run_ts,
    (SELECT 1)                                               AS one,
    ROW(cu.customer_id, cu.status)::text                     AS row_text
FROM crm.customer AS cu
JOIN customer     AS k  ON k.customer_id = cu.customer_id
LEFT JOIN crm.contact AS ct ON ct.customer_id = cu.customer_id
                           AND ct.is_primary
WHERE cu.created_at > TIMESTAMPTZ '2020-01-01 00:00:00+00';

-- T2 · ORDER BY a name that is an OUTPUT alias (segment), not the input column status
SELECT c.segment     AS status,
       c.customer_id AS seg_customer
FROM crm.customer AS c
ORDER BY status
LIMIT 10;

-- T3 · GROUP BY a name that is an INPUT column (c.status), not the output alias
SELECT MAX(c.segment) AS status,
       COUNT(*)       AS n
FROM crm.customer AS c
GROUP BY status;

-- T4 · an INSERT whose literals and comments look like columns and statements
INSERT INTO mart.customer_snapshot (customer_id, customer_name, segment, status, updated_at)
SELECT c.customer_id,
       c.customer_name,
       -- the next column is NOT c.credit_limit; this comment only names it
       c.segment,
       'ACTIVE',            -- a literal, though it sits where status would
       now()
FROM crm.customer AS c
WHERE c.status = 'ACTIVE'
  AND c.customer_name NOT LIKE '%;%'
  AND c.segment <> 'FROM crm.contact';
