-- =========================================================================================
-- H1 · shipment margin fact (hard)                          final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: the shipment-margin sample rebuilt over the final schema - one bare WITH RECURSIVE
-- ... SELECT of 30 CTEs and a final projection over named windows, gold dimensions and a
-- WITH TIES page. PostgreSQL 17. What it mixes:
--   * a VALUES grid with a column list, joined on a RANGE on a null side ........ D-49, D-48
--   * literal relations (unnest of arrays, generate_series of dates): no source  D-28
--   * dedups on the spine: DISTINCT ON (two keys, NULLS LAST), ROW_NUMBER with
--     SIMILAR TO, DENSE_RANK over a named window ................................. D-9
--   * a recursive customer roll-up on the spine, with its cycle guard ........... filter
--   * a one-row FETCH FIRST 1 ROW LATERAL inside a spine CTE ..................... D-14
--   * frames: ROWS, GROUPS ... EXCLUDE CURRENT ROW, an expression partition ..... D-4
--   * a named-window chain (w_cust -> w_cust_date -> w_cust_frame) .............. D-4
--   * LEFT-joined CTEs with their own WHERE (values) and one whose WHERE also
--     decides rows through a NOT IN in the final WHERE (stays a filter only) ..... D-45 + exception
--   * a ranking cut (rank <= 3) inside a LEFT-joined CTE ......................... D-45, D-46
--   * a UNION ALL waterfall of three aggregates, windowed, LEFT-joined with a
--     literal-only ON condition ................................................. D-2, D-48
--   * nested WITH inside a CTE; five levels of derived tables
--   * a SELECT-list scalar subquery over the literal calendar .................... D-12
--   * final: dimension LEFT joins with literal tests, IN / ANY / NOT IN / NOT
--     EXISTS in WHERE, named windows, OFFSET ... FETCH FIRST ... WITH TIES ....... D-52, D-9
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH RECURSIVE
param_grid (region_code, fx_floor, eff_from, eff_to) AS (
    VALUES ('NA',   1.000::numeric, DATE '2020-01-01', DATE '9999-12-31'),
           ('EMEA', 0.921,          DATE '2020-01-01', DATE '2024-06-30'),
           ('EMEA', 0.934,          DATE '2024-07-01', DATE '9999-12-31'),
           ('APAC', 7.123,          DATE '2019-01-01', DATE '9999-12-31')
),
status_map AS (
    SELECT u.status_code, u.status_rank, u.status_desc
    FROM unnest(ARRAY['OPEN', 'SHIPPED', 'INVOICED', 'CLOSED', 'CANCELLED'],
                ARRAY[10, 30, 40, 50, 99],
                ARRAY['Open', 'Shipped', 'Invoiced', 'Closed', 'Cancelled'])
         AS u (status_code, status_rank, status_desc)
),
calendar AS (
    SELECT g.d::date                          AS cal_date,
           EXTRACT(ISODOW FROM g.d) IN (6, 7) AS is_weekend
    FROM generate_series(DATE '2018-01-01', DATE '2030-12-31', INTERVAL '1 day') AS g (d)
),
shp_hdr AS (
    SELECT DISTINCT ON (h.shipment_id, h.source_system)
           h.shipment_id, h.source_system, h.shipment_number, h.customer_id,
           h.ship_from_org_id, h.ship_to_site_id, h.carrier_code, h.currency_code,
           h.status_code, h.header_attrs, h.created_at
    FROM lgx.shipment_header AS h
    WHERE h.is_deleted IS DISTINCT FROM true
      AND h.created_at >= TIMESTAMPTZ '2019-01-01 00:00:00+00'
    ORDER BY h.shipment_id, h.source_system, h.updated_at DESC NULLS LAST, h.version_no DESC
),
shp_line AS (
    SELECT q.*
    FROM (
        SELECT l.*,
               ROW_NUMBER() OVER (PARTITION BY l.shipment_line_id
                                  ORDER BY l.last_update_ts DESC, l.etl_batch_id DESC NULLS FIRST) AS _rn
        FROM lgx.shipment_line AS l
        WHERE l.line_type_code SIMILAR TO '(STD|KIT|SVC)[0-9]{0,2}%'
    ) AS q
    WHERE q._rn = 1
),
item_master AS (
    SELECT im.item_id, im.item_code, im.item_desc, im.item_class, im.std_cost, im.attrs
    FROM ONLY lgx.item_master AS im
),
cust AS (
    SELECT z.customer_id, z.customer_name, z.parent_customer_id, z.country_code, z.segment
    FROM (
        SELECT c.*, DENSE_RANK() OVER w_cust AS _dr
        FROM crm.customer AS c
        WINDOW w_cust AS (PARTITION BY c.customer_id ORDER BY c.valid_from DESC, c.valid_to DESC)
    ) AS z
    WHERE z._dr = 1
),
cust_rollup (customer_id, ultimate_parent_id, hop, chain) AS (
    SELECT cu.customer_id, cu.customer_id, 0, ARRAY[cu.customer_id]
    FROM cust AS cu
    WHERE cu.parent_customer_id IS NULL
    UNION ALL
    SELECT c2.customer_id, r.ultimate_parent_id, r.hop + 1, r.chain || c2.customer_id
    FROM cust AS c2
    JOIN cust_rollup AS r ON c2.parent_customer_id = r.customer_id
    WHERE NOT c2.customer_id = ANY (r.chain)
      AND r.hop <= 15
),
site_geo AS (
    SELECT s.site_id, s.site_code, s.country_code, s.time_zone_name,
           s.address_lines[1]           AS addr1,
           cardinality(s.address_lines) AS addr_lines
    FROM crm.customer_site AS s
    WHERE s.latitude BETWEEN SYMMETRIC 90 AND -90
),
wh_org AS (
    SELECT org_id, w.warehouse_code, w.region_code, o.org_name
    FROM lgx.warehouse AS w
    JOIN crm.organization AS o USING (org_id)
    WHERE o.is_active
),
carrier_dim AS (
    SELECT cr.carrier_code, cr.carrier_name, svc.service_level, svc.transit_days, tm.mode_desc
    FROM (lgx.carrier AS cr
          LEFT JOIN (lgx.carrier_service AS svc
                     JOIN lgx.transport_mode AS tm ON tm.mode_code = svc.mode_code)
            ON svc.carrier_code = cr.carrier_code)
    WHERE cr.active_flag = 'Y'
       OR (cr.active_flag IS NULL AND cr.end_date >= CURRENT_DATE)
),
line_base AS (
    SELECT sl.shipment_line_id, sh.shipment_id, sh.shipment_number, sh.source_system,
           sh.customer_id, cr.ultimate_parent_id, sh.ship_from_org_id, sh.ship_to_site_id,
           sh.carrier_code, sh.currency_code, sh.status_code AS hdr_status, sl.line_status,
           sl.item_id, im.item_code, im.item_class,
           sl.ordered_qty, sl.shipped_qty, COALESCE(sl.cancelled_qty, 0) AS cancelled_qty,
           sl.unit_selling_price AS unit_price, im.std_cost AS unit_cost,
           sl.ship_date, sl.promise_date, sl.request_date,
           sh.created_at AS hdr_created_at, sl.last_update_ts AS line_updated_at,
           sl.line_attrs, sh.header_attrs, cu.segment
    FROM shp_line AS sl
    JOIN shp_hdr  AS sh ON sh.shipment_id = sl.shipment_id
                       AND sh.source_system = sl.source_system
    JOIN cust     AS cu ON cu.customer_id = sh.customer_id
    JOIN cust_rollup AS cr ON cr.customer_id = sh.customer_id
    LEFT JOIN item_master AS im ON im.item_id = sl.item_id
),
line_fx AS (
    SELECT lb.shipment_line_id, fx.conversion_rate AS usd_rate
    FROM line_base AS lb
    LEFT JOIN LATERAL (
        SELECT r.conversion_rate
        FROM gold.fx_rate AS r
        WHERE r.from_ccy = lb.currency_code
          AND r.to_ccy = 'USD'
          AND r.rate_date <= lb.ship_date
        ORDER BY r.rate_date DESC
        FETCH FIRST 1 ROW ONLY
    ) AS fx ON TRUE
),
line_running AS (
    SELECT lb.*,
           f.usd_rate,
           SUM(lb.shipped_qty) OVER (PARTITION BY lb.customer_id
                                     ORDER BY lb.ship_date, lb.shipment_line_id
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cum_qty,
           AVG(lb.unit_price) OVER (PARTITION BY lb.item_id
                                    ORDER BY lb.ship_date
                                    GROUPS BETWEEN 2 PRECEDING AND 2 FOLLOWING
                                    EXCLUDE CURRENT ROW)                              AS avg_price_neighbours,
           SUM(lb.shipped_qty * lb.unit_price) OVER (
               PARTITION BY lb.ultimate_parent_id, date_trunc('month', lb.ship_date)) AS parent_month_revenue
    FROM line_base AS lb
    JOIN line_fx   AS f ON f.shipment_line_id = lb.shipment_line_id
),
line_ranked AS (
    SELECT lr.shipment_line_id,
           ROW_NUMBER()                      OVER w_cust_date  AS rn_cust,
           LAG(lr.unit_price, 1, 0::numeric) OVER w_cust_date  AS prev_price,
           FIRST_VALUE(lr.unit_price)        OVER w_cust_frame AS first_price,
           NTILE(4)                          OVER w_cust_date  AS quartile_cust
    FROM line_running AS lr
    WINDOW w_cust       AS (PARTITION BY lr.customer_id),
           w_cust_date  AS (w_cust ORDER BY lr.ship_date ASC NULLS FIRST, lr.shipment_line_id DESC),
           w_cust_frame AS (w_cust_date ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING)
),
cust_agg AS (
    SELECT lb.customer_id,
           COUNT(*)                                                           AS line_cnt,
           COUNT(DISTINCT lb.item_id)                                         AS distinct_items,
           SUM(lb.shipped_qty) FILTER (WHERE lb.ship_date > lb.promise_date)  AS late_qty,
           string_agg(DISTINCT lb.item_code, ', ' ORDER BY lb.item_code)      AS item_list,
           bool_and(lb.shipped_qty >= lb.ordered_qty)                         AS all_fully_shipped
    FROM line_base AS lb
    GROUP BY lb.customer_id
    HAVING COUNT(*) > 0
),
item_price_stats AS (
    SELECT lb.item_id,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY lb.unit_price) AS median_price,
           mode() WITHIN GROUP (ORDER BY lb.carrier_code)             AS most_common_carrier
    FROM line_base AS lb
    GROUP BY lb.item_id
),
carrier_perf AS (
    SELECT lb.carrier_code,
           date_trunc('month', lb.ship_date)::date                               AS perf_month,
           (COUNT(*) FILTER (WHERE lb.ship_date <= lb.promise_date))::numeric
               / NULLIF(COUNT(*) FILTER (WHERE lb.promise_date IS NOT NULL), 0)  AS otd_ratio
    FROM line_base AS lb
    GROUP BY lb.carrier_code, date_trunc('month', lb.ship_date)
),
best_carrier_month AS (
    SELECT r.carrier_code, r.perf_month, r.otd_ratio, r.otd_rank
    FROM (
        SELECT cp.*,
               RANK() OVER (PARTITION BY cp.perf_month ORDER BY cp.otd_ratio DESC NULLS LAST) AS otd_rank
        FROM carrier_perf AS cp
    ) AS r
    WHERE r.otd_rank <= 3
),
line_attr_flat AS (
    SELECT lb.shipment_line_id,
           lb.line_attrs ->> 'hazmat_class'                                      AS hazmat_class,
           (lb.line_attrs #>> '{dims,length_cm}')::numeric                       AS length_cm,
           lb.line_attrs ? 'lot_no'                                              AS has_lot,
           lb.line_attrs @> '{"temperature_controlled": true}'                   AS is_cold_chain,
           jsonb_path_exists(lb.line_attrs, '$.packages[*] ? (@.weight_kg > 25)') AS is_heavy
    FROM line_base AS lb
    WHERE lb.line_attrs IS NOT NULL
),
hdr_json AS (
    SELECT sh.shipment_id,
           JSON_VALUE(sh.header_attrs, '$.priority' RETURNING int DEFAULT 5 ON EMPTY) AS priority_num,
           sh.header_attrs -> 'contacts' -> 0 ->> 'email'                           AS first_contact_email
    FROM shp_hdr AS sh
),
hdr_charges AS (
    SELECT sh.shipment_id,
           SUM(ch.amount) FILTER (WHERE ch.charge_code LIKE 'FRT%') AS freight_amount
    FROM shp_hdr AS sh
    CROSS JOIN LATERAL jsonb_to_recordset(sh.header_attrs -> 'charges')
         AS ch (charge_code text, amount numeric)
    GROUP BY sh.shipment_id
),
line_classified AS (
    SELECT lb.shipment_line_id,
           CASE
               WHEN lb.hdr_status IN ('CANCELLED', 'VOID') THEN
                   CASE lb.line_status WHEN 'SHIPPED' THEN 'ANOMALY' ELSE 'CANCELLED' END
               WHEN lb.ship_date IS NULL THEN
                   CASE WHEN COALESCE(lb.promise_date, lb.request_date) < CURRENT_DATE
                        THEN 'OPEN:PAST_DUE' ELSE 'OPEN' END
               WHEN lb.ship_date <= lb.promise_date THEN
                   CASE WHEN lb.shipped_qty >= lb.ordered_qty
                        THEN 'ON_TIME:FULL' ELSE 'ON_TIME:PARTIAL' END
               ELSE 'LATE'
           END AS delivery_class,
           CASE sign(lb.unit_price - lb.unit_cost)
               WHEN  1 THEN 'POSITIVE'
               WHEN  0 THEN 'BREAK_EVEN'
               WHEN -1 THEN 'NEGATIVE'
               ELSE 'UNKNOWN'
           END AS margin_class
    FROM line_base AS lb
),
margin_waterfall AS (
    SELECT w.customer_id, w.step_no, w.amount,
           SUM(w.amount) OVER (PARTITION BY w.customer_id ORDER BY w.step_no
                               ROWS UNBOUNDED PRECEDING) AS running_amount
    FROM (
        SELECT lb.customer_id, 1 AS step_no, SUM(lb.shipped_qty * lb.unit_price) AS amount
        FROM line_base AS lb
        GROUP BY lb.customer_id
        UNION ALL
        SELECT lb.customer_id, 2, -SUM(lb.shipped_qty * lb.unit_cost)
        FROM line_base AS lb
        GROUP BY lb.customer_id
        UNION ALL
        SELECT lb.customer_id, 3, -SUM(hc.freight_amount)
        FROM line_base AS lb
        JOIN hdr_charges AS hc ON hc.shipment_id = lb.shipment_id
        GROUP BY lb.customer_id
    ) AS w
),
orphan_checks AS (
    SELECT 'LINE_WITHOUT_ITEM' AS check_name, lb.shipment_line_id AS key_id
    FROM line_base AS lb
    WHERE NOT EXISTS (SELECT 1 FROM item_master AS im WHERE im.item_id = lb.item_id)
    UNION ALL
    SELECT 'LINE_WITHOUT_SITE', lb.shipment_line_id
    FROM line_base AS lb
    WHERE lb.ship_to_site_id NOT IN (SELECT sg.site_id FROM site_geo AS sg)
),
nested_with_demo AS (
    WITH inner_a AS (
             SELECT lb.customer_id, COUNT(*) AS c
             FROM line_base AS lb
             GROUP BY lb.customer_id
         ),
         inner_b AS MATERIALIZED (
             SELECT ia.customer_id, ia.c, NTILE(10) OVER (ORDER BY ia.c DESC) AS decile
             FROM inner_a AS ia
         )
    SELECT ib.customer_id, ib.decile
    FROM inner_b AS ib
),
deep_nesting AS (
    SELECT l5.k, l5.v5
    FROM (
        SELECT l4.k, l4.v4 * 2 AS v5
        FROM (
            SELECT l3.k, SUM(l3.v3) AS v4
            FROM (
                SELECT l2.k, l2.v2 + 1 AS v3
                FROM (
                    SELECT l1.k, l1.v1 AS v2
                    FROM (
                        SELECT lb.customer_id AS k, lb.shipped_qty AS v1
                        FROM line_base AS lb
                    ) AS l1 (k, v1)
                ) AS l2
            ) AS l3
            GROUP BY l3.k
        ) AS l4 (k, v4)
    ) AS l5
),
line_enriched AS (
    SELECT lb.shipment_line_id, lb.shipment_id, lb.shipment_number, lb.source_system,
           lb.source_system || '~' || lb.shipment_line_id::text                       AS integration_id,
           md5(concat_ws('|', lb.source_system, lb.shipment_id, lb.shipment_line_id))  AS row_hash,
           lb.customer_id, lb.ultimate_parent_id, lb.item_id, lb.item_code, lb.carrier_code,
           lb.hdr_status, lb.line_status,
           sm.status_rank                                                             AS line_status_rank,
           lc.delivery_class, lc.margin_class,
           lb.ordered_qty, lb.shipped_qty, lb.cancelled_qty,
           lb.ordered_qty - lb.shipped_qty - lb.cancelled_qty                         AS open_qty,
           (lb.shipped_qty * lb.unit_price * COALESCE(lr.usd_rate, 1))::numeric(38, 10) AS ext_amount_usd,
           (lb.shipped_qty * (lb.unit_price - lb.unit_cost)
               * COALESCE(lr.usd_rate, 1))::numeric(38, 10)                           AS gross_margin_usd,
           ips.median_price                                                           AS item_median_price,
           lr.cum_qty, lr.avg_price_neighbours, lr.parent_month_revenue,
           rk.rn_cust, rk.prev_price, rk.first_price, rk.quartile_cust,
           lb.ship_date, lb.promise_date, lb.hdr_created_at, lb.line_updated_at,
           cal.is_weekend                                                             AS shipped_on_weekend,
           la.hazmat_class, la.is_heavy,
           hj.priority_num,
           wo.org_name                                                                AS ship_from_org_name,
           sg.site_code                                                               AS ship_to_site_code,
           sg.addr1                                                                   AS ship_to_addr1,
           cd.carrier_name, cd.transit_days,
           pg.fx_floor
    FROM line_base AS lb
    JOIN line_running AS lr ON lr.shipment_line_id = lb.shipment_line_id
    JOIN line_ranked  AS rk ON rk.shipment_line_id = lb.shipment_line_id
    LEFT JOIN status_map       AS sm  ON sm.status_code = lb.line_status
    LEFT JOIN line_classified  AS lc  ON lc.shipment_line_id = lb.shipment_line_id
    LEFT JOIN item_price_stats AS ips ON ips.item_id = lb.item_id
    LEFT JOIN calendar         AS cal ON cal.cal_date = lb.ship_date
    LEFT JOIN line_attr_flat   AS la  ON la.shipment_line_id = lb.shipment_line_id
    LEFT JOIN hdr_json         AS hj  ON hj.shipment_id = lb.shipment_id
    LEFT JOIN wh_org           AS wo  ON wo.org_id = lb.ship_from_org_id
    LEFT JOIN site_geo         AS sg  ON sg.site_id = lb.ship_to_site_id
    LEFT JOIN carrier_dim      AS cd  ON cd.carrier_code = lb.carrier_code
    LEFT JOIN param_grid       AS pg
           ON pg.region_code = wo.region_code
          AND lb.hdr_created_at::date BETWEEN pg.eff_from AND pg.eff_to
),
line_overdue AS (
    SELECT le.*,
           CASE
               WHEN le.open_qty <= 0        THEN 0
               WHEN le.promise_date IS NULL THEN NULL
               ELSE GREATEST(CURRENT_DATE - le.promise_date, 0)
           END AS overdue_days,
           (SELECT COUNT(*)
            FROM calendar AS c
            WHERE c.cal_date > le.promise_date
              AND c.cal_date <= COALESCE(le.ship_date, CURRENT_DATE)
              AND NOT c.is_weekend) AS business_days_late
    FROM line_enriched AS le
),
line_with_ctx AS (
    SELECT lo.*,
           SUM(lo.ext_amount_usd) OVER w_c                                   AS cust_total_usd,
           lo.ext_amount_usd / NULLIF(SUM(lo.ext_amount_usd) OVER w_c, 0)    AS share_of_cust,
           RANK() OVER (w_c ORDER BY lo.ext_amount_usd DESC NULLS LAST)      AS rank_in_cust,
           ca.distinct_items                                                 AS cust_distinct_items,
           ca.item_list                                                      AS cust_item_list,
           ca.late_qty                                                       AS cust_late_qty,
           hc.freight_amount,
           mw.running_amount                                                 AS cust_net_margin,
           bcm.otd_ratio                                                     AS carrier_month_otd_ratio,
           nwd.decile                                                        AS cust_volume_decile,
           dn.v5                                                             AS cust_deep_metric
    FROM line_overdue AS lo
    LEFT JOIN cust_agg           AS ca  ON ca.customer_id = lo.customer_id
    LEFT JOIN hdr_charges        AS hc  ON hc.shipment_id = lo.shipment_id
    LEFT JOIN margin_waterfall   AS mw  ON mw.customer_id = lo.customer_id
                                       AND mw.step_no = 3
    LEFT JOIN best_carrier_month AS bcm ON bcm.carrier_code = lo.carrier_code
                                       AND bcm.perf_month = date_trunc('month', lo.ship_date)::date
    LEFT JOIN nested_with_demo   AS nwd ON nwd.customer_id = lo.customer_id
    LEFT JOIN deep_nesting       AS dn  ON dn.k = lo.customer_id
    WINDOW w_c AS (PARTITION BY lo.customer_id)
),
final_src AS (
    SELECT DISTINCT ON (x.integration_id) x.*
    FROM line_with_ctx AS x
    WHERE x.integration_id IS NOT NULL
      AND x.integration_id !~ '^\s*$'
    ORDER BY x.integration_id, x.line_updated_at DESC NULLS LAST, x.row_hash
)
SELECT
    fs.integration_id,
    fs.row_hash,
    fs.shipment_line_id,
    fs.shipment_number,
    CASE fs.source_system WHEN 'EBS' THEN 310 WHEN 'FUSION' THEN 320 ELSE 999 END AS datasource_num_id,
    COALESCE(ds.row_wid, -1)                                            AS status_wid,
    COALESCE(dd_ship.date_wid, 19000101)                                AS ship_date_wid,
    COALESCE(dd_prom.date_wid, 19000101)                                AS promise_date_wid,
    fs.customer_id,
    fs.ultimate_parent_id,
    fs.item_code,
    fs.line_status_rank,
    fs.delivery_class,
    fs.margin_class,
    fs.open_qty,
    fs.ext_amount_usd,
    fs.gross_margin_usd,
    fs.item_median_price,
    fs.cum_qty,
    fs.avg_price_neighbours,
    fs.parent_month_revenue,
    fs.rn_cust,
    fs.prev_price,
    fs.first_price,
    fs.quartile_cust,
    fs.shipped_on_weekend,
    fs.hazmat_class,
    fs.is_heavy,
    fs.priority_num,
    fs.ship_from_org_name,
    fs.ship_to_site_code,
    fs.ship_to_addr1,
    fs.carrier_name,
    fs.transit_days,
    fs.fx_floor,
    fs.overdue_days,
    fs.business_days_late,
    fs.cust_total_usd,
    fs.share_of_cust,
    fs.rank_in_cust,
    fs.cust_distinct_items,
    fs.cust_item_list,
    fs.cust_late_qty,
    fs.freight_amount,
    fs.cust_net_margin,
    fs.carrier_month_otd_ratio,
    fs.cust_volume_decile,
    fs.cust_deep_metric,
    ROW_NUMBER() OVER w_final                                           AS final_row_no,
    LAG(fs.ship_date) OVER w_cust_ship                                  AS prev_ship_date,
    SUM(fs.ext_amount_usd) OVER (w_cust_ship ROWS BETWEEN UNBOUNDED PRECEDING
                                                      AND CURRENT ROW)  AS cust_running_usd,
    COUNT(*) OVER (PARTITION BY fs.shipment_id)                         AS lines_in_shipment,
    jsonb_build_object('id', fs.integration_id, 'class', fs.delivery_class) AS row_summary_json,
    'N'::char(1)                                                        AS delete_flg,
    CURRENT_TIMESTAMP                                                   AS w_insert_dt
FROM final_src AS fs
LEFT JOIN gold.dim_status AS ds
       ON ds.integration_id = 'SHIPMENT_LINE~' || fs.line_status
      AND ds.datasource_num_id = 310
LEFT JOIN gold.dim_date AS dd_ship ON dd_ship.calendar_date = fs.ship_date
LEFT JOIN gold.dim_date AS dd_prom ON dd_prom.calendar_date = fs.promise_date
WHERE fs.hdr_created_at >= TIMESTAMP '2019-01-01' - INTERVAL '1 day'
  AND fs.source_system = ANY (ARRAY['EBS', 'FUSION', 'SAP']::text[])
  AND NOT (fs.hdr_status = 'CANCELLED' AND fs.shipped_qty = 0)
  AND fs.shipment_line_id NOT IN (SELECT oc.key_id FROM orphan_checks AS oc)
  AND NOT EXISTS (SELECT 1
                  FROM evt.flag AS sup
                  WHERE sup.entity_id = fs.shipment_line_id
                    AND sup.flag_code = 'SUPPRESS'
                    AND sup.active_flag)
WINDOW w_final     AS (ORDER BY fs.customer_id, fs.ship_date NULLS LAST, fs.shipment_line_id),
       w_cust      AS (PARTITION BY fs.customer_id),
       w_cust_ship AS (w_cust ORDER BY fs.ship_date ASC NULLS FIRST, fs.shipment_line_id)
ORDER BY fs.customer_id ASC NULLS LAST, fs.ship_date ASC, fs.shipment_line_id
OFFSET 0 ROWS
FETCH FIRST 1000000 ROWS WITH TIES;
