-- =========================================================================================
-- M4 · semi-structured shipment documents (medium)          final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: shipment lines enriched from jsonb documents, arrays, series and an XML feed,
-- one bare SELECT. PostgreSQL 17.
--   * jsonb ->, ->>, #>>, ?, @>, ||, jsonb_build_object, jsonb_path_query_first D-29 derived
--   * SQL/JSON JSON_VALUE ... RETURNING ... DEFAULT ON EMPTY ............... restated, D-29
--   * jsonb_to_recordset with a column list, aggregated, LEFT JOINed ...... D-27, D-29, D-45
--   * JSON_TABLE with FOR ORDINALITY and EXISTS PATH, aggregated .......... D-30, D-26
--   * jsonb_each WITH ORDINALITY, array_agg ORDER BY the ordinal .......... D-26, B1 S11
--   * multi-array unnest WITH ORDINALITY, string_agg ORDER BY the ordinal .. D-26
--   * generate_series between two columns, filtered, counted .............. D-28, D-45
--   * ROWS FROM over two literal series, LEFT JOINed ...................... D-28 (no source)
--   * XMLTABLE with PASSING, plus xpath(), aggregated ...................... D-30, D-29
--   * jsonb_array_elements WITH ORDINALITY, then DISTINCT ON on the null side D-26, D-45
--   * WHERE: jsonb key test, IS NOT NULL, jsonb_typeof ..................... filters
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
hdr AS (
    SELECT h.shipment_id, h.customer_id, h.header_attrs, h.created_at
    FROM lgx.shipment_header AS h
    WHERE h.header_attrs IS NOT NULL
      AND h.header_attrs ? 'packages'
),
charges AS (
    SELECT hd.shipment_id, ch.charge_code, ch.amount, ch.currency
    FROM hdr AS hd
    CROSS JOIN LATERAL jsonb_to_recordset(hd.header_attrs -> 'charges')
         AS ch (charge_code text, amount numeric, currency char(3))
    WHERE ch.amount <> 0
),
charge_summary AS (
    SELECT c.shipment_id,
           SUM(c.amount) FILTER (WHERE c.charge_code LIKE 'FRT%') AS freight_amount,
           jsonb_object_agg(c.charge_code, c.amount)              AS charges_by_code,
           COUNT(*)                                               AS charge_count
    FROM charges AS c
    GROUP BY c.shipment_id
),
packages AS (
    SELECT hd.shipment_id, jt.pkg_ord, jt.pkg_id, jt.pkg_weight, jt.pkg_is_void
    FROM hdr AS hd,
         JSON_TABLE(
             hd.header_attrs, '$.packages[*]'
             COLUMNS (
                 pkg_ord     FOR ORDINALITY,
                 pkg_id      text    PATH '$.id',
                 pkg_weight  numeric PATH '$.weight_kg',
                 pkg_is_void boolean EXISTS PATH '$.void_reason'
             )
         ) AS jt
),
pkg_summary AS (
    SELECT p.shipment_id,
           COUNT(*)                                AS pkg_count,
           SUM(p.pkg_weight)                       AS total_weight,
           COUNT(*) FILTER (WHERE p.pkg_is_void)   AS void_count,
           MAX(p.pkg_ord)                          AS max_pkg_ord
    FROM packages AS p
    GROUP BY p.shipment_id
),
line_kv AS (
    SELECT l.shipment_line_id, kv.key AS attr_key, kv.value AS attr_val, kv.ord AS attr_ord
    FROM lgx.shipment_line AS l
    CROSS JOIN LATERAL jsonb_each(COALESCE(l.line_attrs, '{}'::jsonb))
         WITH ORDINALITY AS kv (key, value, ord)
    WHERE kv.key !~ '^_'
),
line_kv_summary AS (
    SELECT k.shipment_line_id,
           array_agg(k.attr_key ORDER BY k.attr_ord)  AS attr_keys,
           jsonb_object_agg(k.attr_key, k.attr_val)   AS attrs_clean
    FROM line_kv AS k
    GROUP BY k.shipment_line_id
),
tag_rows AS (
    SELECT im.item_id, u.tag, u.band, u.n
    FROM lgx.item_master AS im
    CROSS JOIN LATERAL unnest(im.tags, ARRAY['A', 'B', 'C'])
         WITH ORDINALITY AS u (tag, band, n)
),
tag_summary AS (
    SELECT t.item_id, string_agg(t.tag, ',' ORDER BY t.n) AS tag_list
    FROM tag_rows AS t
    WHERE t.tag IS NOT NULL
    GROUP BY t.item_id
),
promise_days AS (
    SELECT l2.shipment_line_id, COUNT(*) AS business_days
    FROM lgx.shipment_line AS l2
    CROSS JOIN LATERAL generate_series(l2.promise_date, l2.ship_date, INTERVAL '1 day') AS g (d)
    WHERE EXTRACT(ISODOW FROM g.d) < 6
    GROUP BY l2.shipment_line_id
),
calendar AS (
    SELECT r.d::date AS cal_date, r.flag
    FROM ROWS FROM (
             generate_series(DATE '2024-01-01', DATE '2026-12-31', INTERVAL '1 day'),
             unnest(ARRAY['N', 'N', 'N', 'N', 'N', 'Y', 'Y'])
         ) AS r (d, flag)
),
edi AS (
    SELECT e.edi_doc_id,
           x.sku,
           x.qty,
           (xpath('/Order/@id', e.payload))[1]::text AS order_ref
    FROM lgx.edi_document AS e,
         XMLTABLE('/Order/Line' PASSING e.payload
                  COLUMNS sku text    PATH 'Sku',
                          qty numeric PATH 'Qty') AS x
    WHERE e.doc_status = 'VALIDATED'
),
contacts AS (
    SELECT hd.shipment_id,
           c.value ->> 'email' AS email,
           c.value ->> 'role'  AS role,
           c.n
    FROM hdr AS hd
    CROSS JOIN LATERAL jsonb_array_elements(hd.header_attrs -> 'contacts')
         WITH ORDINALITY AS c (value, n)
),
ship_to_contact AS (
    SELECT DISTINCT ON (ct.shipment_id) ct.shipment_id, ct.email
    FROM contacts AS ct
    WHERE ct.role = 'SHIP_TO'
    ORDER BY ct.shipment_id, ct.n
),
edi_summary AS (
    SELECT ed.sku,
           SUM(ed.qty)                                  AS edi_qty,
           string_agg(DISTINCT ed.order_ref, ',')       AS edi_orders
    FROM edi AS ed
    GROUP BY ed.sku
)
SELECT
    l.shipment_line_id,
    hd.shipment_id,
    hd.header_attrs ->> 'priority'                                       AS priority,
    (hd.header_attrs #>> '{delivery,window_start}')::timestamptz         AS delivery_window_start,
    hd.header_attrs -> 'contacts' -> 0 ->> 'email'                       AS first_contact_email,
    jsonb_path_query_first(hd.header_attrs, '$.insurance.policy_no')     AS policy_no,
    JSON_VALUE(hd.header_attrs, '$.service.level'
               RETURNING text DEFAULT 'STD' ON EMPTY)                    AS service_level,
    l.line_attrs @> '{"hazmat": true}'                                   AS is_hazmat,
    im.attrs || jsonb_build_object('item_code', im.item_code)            AS item_doc,
    to_jsonb(ARRAY[l.shipped_qty, l.ordered_qty])                        AS qty_pair,
    cs.freight_amount,
    cs.charges_by_code,
    cs.charge_count,
    ps.pkg_count,
    ps.total_weight,
    ps.void_count,
    ps.max_pkg_ord,
    kvs.attr_keys,
    kvs.attrs_clean,
    ts.tag_list,
    pd.business_days,
    cal.flag                                                             AS holiday_flag,
    es.edi_qty,
    es.edi_orders,
    stc.email                                                            AS ship_to_email
FROM hdr AS hd
JOIN lgx.shipment_line AS l  ON l.shipment_id = hd.shipment_id
JOIN lgx.item_master   AS im ON im.item_id = l.item_id
LEFT JOIN charge_summary  AS cs  ON cs.shipment_id = hd.shipment_id
LEFT JOIN pkg_summary     AS ps  ON ps.shipment_id = hd.shipment_id
LEFT JOIN line_kv_summary AS kvs ON kvs.shipment_line_id = l.shipment_line_id
LEFT JOIN tag_summary     AS ts  ON ts.item_id = l.item_id
LEFT JOIN promise_days    AS pd  ON pd.shipment_line_id = l.shipment_line_id
LEFT JOIN calendar        AS cal ON cal.cal_date = l.ship_date
LEFT JOIN edi_summary     AS es  ON es.sku = im.item_code
LEFT JOIN ship_to_contact AS stc ON stc.shipment_id = hd.shipment_id
WHERE l.line_attrs IS NOT NULL
  AND jsonb_typeof(l.line_attrs -> 'dims') = 'object';
