-- =========================================================================================
-- H4 · nightly shipment load script (hard)                  final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: one script of many statements, chained through two temp tables, the way a
-- scheduler runs a nightly load. PostgreSQL 17. Every relation outside pg_temp is
-- schema-qualified; the temp tables are read by bare name (D-31: pg_temp shadows).
--   S1  CREATE TEMP TABLE ... AS a five-CTE staging query ................... D-31 node
--   S2  UPDATE mart.shipment_fact ... FROM the temp table, change-detecting .. D-1, D-11
--   S3  INSERT new lines, WHERE NOT EXISTS against the target itself ......... D-52
--   S4  DELETE ... USING the source lines that were voided .................. D-1
--   S5  a writable CTE: UPDATE ... RETURNING feeding an audit INSERT ......... D-16, D-17
--   S6  MERGE the customer snapshot from the fact (aggregated USING),
--       MATCHED AND on an aggregate, NOT MATCHED, NOT MATCHED BY SOURCE ...... B3 S15, D-6
--   S7  TRUNCATE the audit table - refused by name (B3); later statements must
--       still be analysed
--   S8  INSERT into the audit table after the refusal
--   S9  CREATE TEMP TABLE of customer metrics: windows over aggregates ....... D-46
--   S10 UPDATE the snapshot from the second temp table ...................... D-1
--   S11 a reconciliation SELECT: FULL JOIN of the fact and the temp table .... COALESCE keys
--   S12 CREATE TEMP TABLE of entity state: DISTINCT ON + two LEFT-joined
--       aggregates with their own WHERE ...................................... D-45
--   S13 MERGE the entity state; INSERT VALUES with no column list ............ positional
--   S14 DELETE stale entities WHERE NOT EXISTS in the temp table ............. D-52
--   S15 an audit row with no column source ................................... nothing
--   S16 INSERT supplier spend ... ON CONFLICT DO NOTHING, positional GROUP BY,
--       ROW_NUMBER over SUM ................................................... D-46
--   S17 SCD2 close: UPDATE gold.dim_employee FROM s2p.person ................. D-1
--   S18 SCD2 open: INSERT new versions, a surrogate from MAX() + ROW_NUMBER,
--       WHERE NOT EXISTS ...................................................... D-12, D-52
--   plus BEGIN / SET LOCAL / ANALYZE / COMMIT, which carry no lineage.
-- Expected: one refusal (S7, TRUNCATE). No boundary otherwise.
-- =========================================================================================
BEGIN;

SET LOCAL work_mem = '256MB';

-- -----------------------------------------------------------------------------------------
-- S1 · stage the last three days of shipment lines
-- -----------------------------------------------------------------------------------------
CREATE TEMP TABLE stg_ship AS
WITH
hdr AS (
    SELECT DISTINCT ON (h.shipment_id)
           h.shipment_id, h.customer_id, h.carrier_code, h.currency_code
    FROM lgx.shipment_header AS h
    WHERE h.is_deleted IS NOT TRUE
    ORDER BY h.shipment_id, h.version_no DESC
),
ln AS (
    SELECT l.shipment_line_id, l.shipment_id, l.item_id, l.line_status,
           l.shipped_qty, l.unit_selling_price, l.ship_date, l.promise_date
    FROM lgx.shipment_line AS l
    WHERE l.last_update_ts >= CURRENT_DATE - 3
),
fx AS (
    SELECT DISTINCT ON (r.from_ccy, r.rate_date)
           r.from_ccy, r.rate_date, r.conversion_rate
    FROM gold.fx_rate AS r
    WHERE r.to_ccy = 'USD'
    ORDER BY r.from_ccy, r.rate_date, r.loaded_at DESC
),
im AS (
    SELECT i.item_id, i.std_cost, i.item_class
    FROM lgx.item_master AS i
),
pkg AS (
    SELECT pe.shipment_id,
           COUNT(*) FILTER (WHERE pe.event_type = 'PACKED') AS packages_packed,
           MAX(pe.event_ts)                                  AS last_event_ts
    FROM lgx.package_event AS pe
    GROUP BY pe.shipment_id
)
SELECT
    ln.shipment_line_id,
    hdr.shipment_id,
    hdr.customer_id,
    ln.item_id,
    ln.line_status,
    ln.ship_date,
    ln.shipped_qty,
    ln.shipped_qty * ln.unit_selling_price * COALESCE(fx.conversion_rate, 1)          AS ext_amount_usd,
    ln.shipped_qty * (ln.unit_selling_price - COALESCE(im.std_cost, 0))
        * COALESCE(fx.conversion_rate, 1)                                             AS gross_margin_usd,
    CASE
        WHEN ln.ship_date IS NULL           THEN 'OPEN'
        WHEN ln.ship_date <= ln.promise_date THEN 'ON_TIME'
        ELSE 'LATE'
    END                                                                               AS delivery_class,
    ln.ship_date > ln.promise_date                                                    AS is_late,
    cr.carrier_name,
    COALESCE(pkg.packages_packed, 0)                                                  AS packages_packed,
    pkg.last_event_ts
FROM hdr
JOIN ln                   ON ln.shipment_id = hdr.shipment_id
LEFT JOIN fx              ON fx.from_ccy = hdr.currency_code
                         AND fx.rate_date = ln.ship_date
LEFT JOIN im              ON im.item_id = ln.item_id
LEFT JOIN lgx.carrier AS cr ON cr.carrier_code = hdr.carrier_code
LEFT JOIN pkg             ON pkg.shipment_id = hdr.shipment_id;

ANALYZE stg_ship;

-- -----------------------------------------------------------------------------------------
-- S2 · refresh changed lines in the fact
-- -----------------------------------------------------------------------------------------
UPDATE mart.shipment_fact AS f
SET shipped_qty      = s.shipped_qty,
    ext_amount_usd   = s.ext_amount_usd,
    gross_margin_usd = s.gross_margin_usd,
    delivery_class   = s.delivery_class,
    is_late          = s.is_late,
    load_ts          = now()
FROM stg_ship AS s
WHERE f.shipment_line_id = s.shipment_line_id
  AND (f.ext_amount_usd IS DISTINCT FROM s.ext_amount_usd
       OR f.delivery_class IS DISTINCT FROM s.delivery_class);

-- -----------------------------------------------------------------------------------------
-- S3 · insert lines the fact has not seen
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.shipment_fact
    (shipment_line_id, shipment_id, customer_id, item_id, ship_date, shipped_qty,
     ext_amount_usd, gross_margin_usd, delivery_class, is_late, load_ts)
SELECT s.shipment_line_id,
       s.shipment_id,
       s.customer_id,
       s.item_id,
       s.ship_date,
       s.shipped_qty,
       s.ext_amount_usd,
       s.gross_margin_usd,
       s.delivery_class,
       s.is_late,
       now()
FROM stg_ship AS s
WHERE s.line_status <> 'VOID'
  AND NOT EXISTS (SELECT 1
                  FROM mart.shipment_fact AS f
                  WHERE f.shipment_line_id = s.shipment_line_id);

-- -----------------------------------------------------------------------------------------
-- S4 · remove voided and cancelled lines
-- -----------------------------------------------------------------------------------------
DELETE FROM mart.shipment_fact AS f
USING lgx.shipment_line AS l
WHERE l.shipment_line_id = f.shipment_line_id
  AND l.line_status IN ('VOID', 'CANCELLED');

-- -----------------------------------------------------------------------------------------
-- S5 · close the snapshots of closed customers, and audit each one
-- -----------------------------------------------------------------------------------------
WITH closed AS (
    UPDATE mart.customer_snapshot AS cs
    SET status = 'CLOSED',
        updated_at = now()
    WHERE cs.customer_id IN (SELECT c.customer_id
                             FROM crm.customer AS c
                             WHERE c.status = 'CLOSED')
    RETURNING cs.customer_id, cs.lifetime_revenue
)
INSERT INTO mart.load_audit (run_id, table_name, rows_affected, run_ts)
SELECT cl.customer_id, 'customer_snapshot', 1, now()
FROM closed AS cl;

-- -----------------------------------------------------------------------------------------
-- S6 · reconcile the customer snapshot from the fact
-- -----------------------------------------------------------------------------------------
MERGE INTO mart.customer_snapshot AS t
USING (
    SELECT f.customer_id,
           c.customer_name,
           c.segment,
           SUM(f.ext_amount_usd)                             AS lifetime_revenue,
           MAX(f.ship_date)                                  AS last_ship_date,
           COUNT(*) FILTER (WHERE f.delivery_class = 'OPEN') AS open_lines
    FROM mart.shipment_fact AS f
    JOIN crm.customer       AS c ON c.customer_id = f.customer_id
    WHERE c.status <> 'CLOSED'
    GROUP BY f.customer_id, c.customer_name, c.segment
) AS s
ON t.customer_id = s.customer_id
WHEN MATCHED AND s.lifetime_revenue IS DISTINCT FROM t.lifetime_revenue THEN
    UPDATE SET lifetime_revenue = s.lifetime_revenue,
               last_ship_date   = s.last_ship_date,
               open_lines       = s.open_lines,
               segment          = s.segment,
               updated_at       = now()
WHEN NOT MATCHED THEN
    INSERT (customer_id, customer_name, segment, lifetime_revenue, last_ship_date,
            open_lines, status, updated_at)
    VALUES (s.customer_id, s.customer_name, s.segment, s.lifetime_revenue,
            s.last_ship_date, s.open_lines, 'ACTIVE', now())
WHEN NOT MATCHED BY SOURCE AND t.status = 'ACTIVE' THEN
    UPDATE SET status = 'DORMANT';

TRUNCATE mart.load_audit;

-- -----------------------------------------------------------------------------------------
-- S8 · audit the fact load, after the refusal above
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.load_audit (run_id, table_name, rows_affected, run_ts)
SELECT EXTRACT(EPOCH FROM now())::bigint,
       'shipment_fact',
       COUNT(*),
       MAX(f.load_ts)
FROM mart.shipment_fact AS f
WHERE f.load_ts >= CURRENT_DATE;

-- -----------------------------------------------------------------------------------------
-- S9 · customer metrics: windows over per-customer aggregates
-- -----------------------------------------------------------------------------------------
CREATE TEMP TABLE cust_metrics AS
WITH
per_customer AS (
    SELECT f.customer_id,
           SUM(f.ext_amount_usd)                                AS revenue,
           SUM(f.gross_margin_usd)                              AS margin,
           COUNT(*) FILTER (WHERE f.is_late)                    AS late_lines,
           COUNT(*)                                             AS lines
    FROM mart.shipment_fact AS f
    WHERE f.ship_date >= CURRENT_DATE - 365
    GROUP BY f.customer_id
),
ranked AS (
    SELECT pc.*,
           RANK() OVER (ORDER BY pc.revenue DESC)               AS revenue_rank,
           pc.margin / NULLIF(pc.revenue, 0)                    AS margin_pct,
           pc.late_lines::numeric / NULLIF(pc.lines, 0)         AS late_ratio
    FROM per_customer AS pc
)
SELECT r.customer_id,
       r.revenue,
       r.margin_pct,
       r.late_ratio,
       r.revenue_rank,
       CASE
           WHEN r.revenue_rank <= 10  THEN 'KEY'
           WHEN r.late_ratio > 0.2    THEN 'AT_RISK'
           ELSE 'STANDARD'
       END                                                      AS segment_hint
FROM ranked AS r;

-- -----------------------------------------------------------------------------------------
-- S10 · push the metrics into the snapshot
-- -----------------------------------------------------------------------------------------
UPDATE mart.customer_snapshot AS t
SET segment          = COALESCE(m.segment_hint, t.segment),
    lifetime_revenue = m.revenue
FROM cust_metrics AS m
WHERE m.customer_id = t.customer_id
  AND t.status <> 'CLOSED';

-- -----------------------------------------------------------------------------------------
-- S11 · reconciliation: fact against staging
-- -----------------------------------------------------------------------------------------
SELECT COALESCE(f.shipment_line_id, s.shipment_line_id)        AS shipment_line_id,
       f.ext_amount_usd                                         AS fact_amount,
       s.ext_amount_usd                                         AS staged_amount,
       COALESCE(f.ext_amount_usd, 0) - COALESCE(s.ext_amount_usd, 0) AS amount_gap,
       CASE
           WHEN f.shipment_line_id IS NULL THEN 'MISSING_IN_FACT'
           WHEN s.shipment_line_id IS NULL THEN 'NOT_STAGED'
           ELSE 'BOTH'
       END                                                      AS presence
FROM mart.shipment_fact AS f
FULL JOIN stg_ship AS s ON s.shipment_line_id = f.shipment_line_id
WHERE f.ext_amount_usd IS DISTINCT FROM s.ext_amount_usd
ORDER BY 1;

-- -----------------------------------------------------------------------------------------
-- S12 · phase 2: stage the entity state
-- -----------------------------------------------------------------------------------------
CREATE TEMP TABLE stg_entity AS
WITH
latest AS (
    SELECT DISTINCT ON (e.entity_id) e.entity_id, e.status, e.amount, e.created_at
    FROM evt.entity_event AS e
    ORDER BY e.entity_id, e.created_at DESC
),
acts AS (
    SELECT a.entity_id, SUM(a.amount) AS activity_total, MAX(a.created_at) AS last_activity
    FROM evt.activity AS a
    WHERE a.kind <> 'NOTE'
    GROUP BY a.entity_id
),
flags AS (
    SELECT fg.entity_id, COUNT(*) AS flag_count
    FROM evt.flag AS fg
    WHERE fg.active_flag
    GROUP BY fg.entity_id
)
SELECT l.entity_id,
       l.status,
       l.amount,
       COALESCE(a.activity_total, 0)            AS activity_total,
       COALESCE(fl.flag_count, 0)               AS flag_count,
       GREATEST(l.created_at, a.last_activity)  AS last_seen
FROM latest AS l
LEFT JOIN acts  AS a  ON a.entity_id = l.entity_id
LEFT JOIN flags AS fl ON fl.entity_id = l.entity_id
WHERE l.status <> 'DELETED';

-- -----------------------------------------------------------------------------------------
-- S13 · merge the entity state
-- -----------------------------------------------------------------------------------------
MERGE INTO mart.entity_state AS t
USING stg_entity AS s
ON t.entity_id = s.entity_id
WHEN MATCHED AND (t.amount IS DISTINCT FROM s.amount OR t.status IS DISTINCT FROM s.status) THEN
    UPDATE SET status         = s.status,
               amount         = s.amount,
               activity_total = s.activity_total,
               flag_count     = s.flag_count,
               last_seen      = s.last_seen
WHEN NOT MATCHED THEN
    INSERT VALUES (s.entity_id, s.status, s.amount, s.activity_total, s.flag_count, s.last_seen);

-- -----------------------------------------------------------------------------------------
-- S14 · drop entities no longer staged and unseen for a year
-- -----------------------------------------------------------------------------------------
DELETE FROM mart.entity_state AS t
WHERE NOT EXISTS (SELECT 1 FROM stg_entity AS s WHERE s.entity_id = t.entity_id)
  AND t.last_seen < CURRENT_DATE - 365;

-- -----------------------------------------------------------------------------------------
-- S15 · audit phase 2
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.load_audit (run_id, table_name, rows_affected, run_ts)
SELECT 2, 'entity_state', COUNT(*), now()
FROM stg_entity;

-- -----------------------------------------------------------------------------------------
-- S16 · phase 3: supplier spend for approved orders
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.supplier_spend
    (supplier_id, supplier_name, spend_year, po_count, total_spend, total_savings, spend_rank)
WITH spend AS (
    SELECT h.vendor_id,
           EXTRACT(YEAR FROM h.created_at)::int  AS spend_year,
           COUNT(DISTINCT h.po_header_id)        AS po_count,
           SUM(l.amount)                         AS total_spend
    FROM s2p.po_header AS h
    JOIN s2p.po_line   AS l ON l.po_header_id = h.po_header_id
    WHERE h.document_status = 'APPROVED'
    GROUP BY 1, 2
)
SELECT sp.vendor_id,
       su.vendor_name,
       sp.spend_year,
       sp.po_count,
       sp.total_spend,
       NULL,
       ROW_NUMBER() OVER (PARTITION BY sp.spend_year ORDER BY sp.total_spend DESC)
FROM spend AS sp
JOIN s2p.supplier AS su ON su.vendor_id = sp.vendor_id
WHERE su.status = 'ACTIVE'
ON CONFLICT DO NOTHING;

-- -----------------------------------------------------------------------------------------
-- S17 · SCD2: close employee versions whose person changed or left
-- -----------------------------------------------------------------------------------------
UPDATE gold.dim_employee AS d
SET effective_to_date = now(),
    is_delete         = 'Y'
FROM s2p.person AS p
WHERE d.integration_id = 'PER~' || p.person_id::text
  AND d.effective_to_date > now()
  AND (d.full_name IS DISTINCT FROM p.full_name OR p.active_flag = 'N');

-- -----------------------------------------------------------------------------------------
-- S18 · SCD2: open a version for every active person without one
-- -----------------------------------------------------------------------------------------
INSERT INTO gold.dim_employee
    (employee_sk, integration_id, full_name, effective_from_date, effective_to_date,
     is_delete, datasource_num_id)
SELECT (SELECT COALESCE(MAX(e.employee_sk), 0) FROM gold.dim_employee AS e)
           + ROW_NUMBER() OVER (ORDER BY p.person_id),
       'PER~' || p.person_id::text,
       p.full_name,
       now(),
       TIMESTAMPTZ '9999-12-31 00:00:00+00',
       'N',
       100
FROM s2p.person AS p
WHERE p.active_flag = 'Y'
  AND NOT EXISTS (SELECT 1
                  FROM gold.dim_employee AS d
                  WHERE d.integration_id = 'PER~' || p.person_id::text
                    AND d.is_delete = 'N');

COMMIT;
