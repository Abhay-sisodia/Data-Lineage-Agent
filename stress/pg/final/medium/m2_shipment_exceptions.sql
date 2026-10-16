-- =========================================================================================
-- M2 · shipment exception board (medium)                    final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: one row per open shipment line, decorated by subqueries in every position, one
-- bare SELECT. PostgreSQL 17.
--   * lookup scalar subqueries, plain and ORDER BY ... LIMIT 1 ............ D-12, D-13
--   * aggregating scalar subqueries over a correlated WHERE ................ D-12 aggregated
--   * EXISTS in the SELECT list ............................................ D-12 conditional
--   * LEFT JOIN LATERAL ... ORDER BY ... LIMIT 1 (one row per outer row) ... D-14 values/influence
--   * the same source reached as a value AND through a predicate ........... D-51
--   * a LEFT-joined aggregate whose own WHERE reads a main-path CTE ........ D-45 (+ exception)
--   * a LEFT-joined CTE holding EXISTS and NOT IN (... EXCEPT ...) .......... D-45, D-52 as values
--   * WHERE: IN over a UNION, EXISTS over an INTERSECT, NOT (= ANY),
--     >= SOME with a correlation ............................................ D-52 filters
--   * a LEFT-joined monthly carrier aggregate: FILTER counts, percentile_disc,
--     a ratio of two aggregates, keyed on an expression ...................... D-10, D-45
--   * RANK over SUM in a LEFT-joined CTE .................................. D-46
--   * an IN subquery nested inside an aggregating scalar subquery ........... D-45 aggregated
--   * ORDER BY ... LIMIT over the whole result .............................. D-9
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
hdr AS (
    SELECT DISTINCT ON (h.shipment_id)
           h.shipment_id, h.customer_id, h.carrier_code, h.currency_code, h.created_at
    FROM lgx.shipment_header AS h
    WHERE h.is_deleted IS NOT TRUE
    ORDER BY h.shipment_id, h.version_no DESC
),
ln AS (
    SELECT l.shipment_line_id, l.shipment_id, l.item_id, l.shipped_qty,
           l.unit_selling_price, l.ship_date, l.promise_date
    FROM lgx.shipment_line AS l
    WHERE l.line_status NOT IN ('CANCELLED', 'VOID')
),
base AS (
    SELECT hdr.shipment_id, hdr.customer_id, hdr.carrier_code, hdr.currency_code,
           ln.shipment_line_id, ln.item_id, ln.shipped_qty, ln.unit_selling_price,
           ln.ship_date, ln.promise_date
    FROM hdr
    JOIN ln ON ln.shipment_id = hdr.shipment_id
),
late_lines AS (
    SELECT b2.customer_id,
           COUNT(*)                            AS late_line_count,
           MAX(b2.ship_date - b2.promise_date) AS worst_delay_days
    FROM base AS b2
    WHERE b2.ship_date > b2.promise_date
    GROUP BY b2.customer_id
),
flagged_customers AS (
    SELECT c.customer_id, c.segment
    FROM crm.customer AS c
    WHERE EXISTS (SELECT 1
                  FROM evt.flag AS f
                  WHERE f.entity_id = c.customer_id
                    AND f.active_flag)
      AND c.customer_id NOT IN (SELECT ct.customer_id
                                FROM crm.contact AS ct
                                WHERE ct.role = 'BLOCKED'
                                EXCEPT
                                SELECT ct2.customer_id
                                FROM crm.contact AS ct2
                                WHERE ct2.is_primary)
),
carrier_perf AS (
    SELECT b3.carrier_code,
           date_trunc('month', b3.ship_date)                                   AS perf_month,
           COUNT(*)                                                            AS shipments,
           COUNT(*) FILTER (WHERE b3.ship_date <= b3.promise_date)             AS on_time,
           (COUNT(*) FILTER (WHERE b3.ship_date <= b3.promise_date))::numeric
               / NULLIF(COUNT(*), 0)                                           AS otd_ratio,
           percentile_disc(0.9) WITHIN GROUP (ORDER BY b3.ship_date - b3.promise_date) AS p90_delay
    FROM base AS b3
    WHERE b3.promise_date IS NOT NULL
    GROUP BY b3.carrier_code, date_trunc('month', b3.ship_date)
),
customer_rank AS (
    SELECT b4.customer_id,
           SUM(b4.shipped_qty * b4.unit_selling_price)                              AS revenue,
           RANK() OVER (ORDER BY SUM(b4.shipped_qty * b4.unit_selling_price) DESC)  AS revenue_rank
    FROM base AS b4
    GROUP BY b4.customer_id
)
SELECT
    b.shipment_line_id,
    b.shipment_id,
    b.customer_id,
    (SELECT c.customer_name
     FROM crm.customer AS c
     WHERE c.customer_id = b.customer_id)                              AS customer_name,
    (SELECT ct.email
     FROM crm.contact AS ct
     WHERE ct.customer_id = b.customer_id
     ORDER BY ct.is_primary DESC, ct.created_at
     LIMIT 1)                                                          AS primary_email,
    (SELECT count(*)
     FROM lgx.package_event AS pe2
     WHERE pe2.shipment_id = b.shipment_id
       AND pe2.event_type = 'EXCEPTION')                               AS exception_count,
    (SELECT max(pe3.event_ts)
     FROM lgx.package_event AS pe3
     WHERE pe3.shipment_id = b.shipment_id)                            AS last_scan_ts,
    EXISTS (SELECT 1
            FROM lgx.package_event AS pe4
            WHERE pe4.shipment_id = b.shipment_id
              AND pe4.event_type = 'DELIVERED')                        AS is_delivered,
    le.event_type                                                      AS last_event_type,
    le.event_ts                                                        AS last_event_ts,
    le.location_code                                                   AS last_location,
    b.shipped_qty * b.unit_selling_price * COALESCE(fx.conversion_rate, 1) AS ext_amount_usd,
    fx.conversion_rate                                                 AS usd_rate,
    ll.late_line_count,
    ll.worst_delay_days,
    fc.segment                                                         AS flagged_segment,
    fc.customer_id IS NOT NULL                                         AS is_flagged,
    CASE
        WHEN ll.late_line_count > 5 AND fc.customer_id IS NOT NULL THEN 'ESCALATE'
        WHEN ll.late_line_count > 5                                THEN 'WATCH'
        ELSE 'OK'
    END                                                                AS risk_class,
    cp.otd_ratio                                                       AS carrier_otd_ratio,
    cp.p90_delay                                                       AS carrier_p90_delay,
    cr.revenue                                                         AS customer_revenue,
    cr.revenue_rank                                                    AS customer_revenue_rank,
    (SELECT count(*)
     FROM lgx.shipment_line AS l9
     WHERE l9.shipment_id = b.shipment_id
       AND l9.item_id IN (SELECT im2.item_id
                          FROM lgx.item_master AS im2
                          WHERE im2.item_class = 'HAZMAT'))            AS hazmat_lines
FROM base AS b
LEFT JOIN LATERAL (
    SELECT pe.event_type, pe.event_ts, pe.location_code
    FROM lgx.package_event AS pe
    WHERE pe.shipment_id = b.shipment_id
      AND pe.event_type <> 'NOTE'
    ORDER BY pe.event_ts DESC
    LIMIT 1
) AS le ON TRUE
LEFT JOIN LATERAL (
    SELECT r.conversion_rate
    FROM gold.fx_rate AS r
    WHERE r.from_ccy = b.currency_code
      AND r.to_ccy = 'USD'
      AND r.rate_date <= b.ship_date
    ORDER BY r.rate_date DESC
    LIMIT 1
) AS fx ON TRUE
LEFT JOIN late_lines        AS ll ON ll.customer_id = b.customer_id
LEFT JOIN flagged_customers AS fc ON fc.customer_id = b.customer_id
LEFT JOIN carrier_perf      AS cp ON cp.carrier_code = b.carrier_code
                                 AND cp.perf_month = date_trunc('month', b.ship_date)
LEFT JOIN customer_rank     AS cr ON cr.customer_id = b.customer_id
WHERE b.carrier_code IN (SELECT cr.carrier_code FROM lgx.carrier AS cr WHERE cr.active_flag = 'Y'
                         UNION
                         SELECT cs.carrier_code FROM lgx.carrier_service AS cs WHERE cs.transit_days <= 5)
  AND EXISTS (SELECT 1 FROM crm.contact AS ct3 WHERE ct3.customer_id = b.customer_id
              INTERSECT
              SELECT 1 FROM crm.customer AS cu WHERE cu.customer_id = b.customer_id
                                                AND cu.status = 'ACTIVE')
  AND NOT (b.item_id = ANY (SELECT bm.component_item_id
                            FROM lgx.bill_of_material AS bm
                            WHERE bm.bom_status = 'OBSOLETE'))
  AND b.unit_selling_price >= SOME (SELECT im.std_cost
                                    FROM lgx.item_master AS im
                                    WHERE im.item_id = b.item_id)
ORDER BY b.shipment_id, b.shipment_line_id
LIMIT 5000;
