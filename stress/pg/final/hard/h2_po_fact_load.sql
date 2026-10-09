-- =========================================================================================
-- H2 · purchase-order fact load: upsert, CTAS summary, mart load (hard)
--                                                           final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: the purchase-order sample (fact_purchase_order_line) translated to PostgreSQL and
-- written as a load: S1 upserts mart.fact_po_line from the whole CTE chain, S2 summarises the
-- fact with CREATE TABLE AS, S3 loads mart.supplier_spend from the table S2 created.
--   S1 INSERT ... SELECT ... ON CONFLICT (integration_id) DO UPDATE
--      * the M1 chain (dedup spine, null-side re-joins, CASE over CASE) ......... as M1
--      * a reference CTE: LEFT joins to category (twice), uom, and a person CTE
--        with its own WHERE ................................................. D-45
--      * a null-side ROW_NUMBER version pick feeding an overdue fallback ....... D-45, D-47
--      * a cost-avoidance sibling of the spine, LEFT JOINed back ............... D-45 exception
--      * a one-row FX LATERAL scaling savings .................................. D-14
--      * the D-7 ladder: one source derived, the rest conditional, one target .. D-7, D-51
--      * EXCLUDED.c, a GREATEST self-edge, a DO UPDATE WHERE ................... D-15
--   S2 CREATE TABLE AS: GROUP BY, COUNT(DISTINCT), FILTER, RANK over SUM ........ D-10, D-46
--   S3 INSERT from the CTAS table (a node, D-31), cut on its rank .............. filter
--   S4 UPDATE ... FROM a requisition dedup (ROW_NUMBER cut in an INNER ON) with
--      a GREATEST self-edge and a CASE that keeps the old value ............... D-1, D-48, D-9
--   S5 DELETE ... USING: fully cancelled distributions ........................ D-1, D-11
--   S6 MERGE from the CTAS table: MATCHED AND, NOT MATCHED, BY SOURCE ......... B3 S15
--   S7 a validation SELECT over the fact: GROUPING SETS, FILTER, HAVING ....... D-50, D-10
--   S8 a yearly history INSERT ... ON CONFLICT DO NOTHING: the fact joined back
--      to its source header, DENSE_RANK over SUM, a cut on the grouping key .... D-46, D-6
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
-- -----------------------------------------------------------------------------------------
-- S1 · upsert the fact from the whole chain
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.fact_po_line AS f (
    integration_id, po_header_id, po_number, line_number, supplier_id, buyer_id,
    item_description, line_amt, po_dist_qty, should_cost, savings_amt, x_po_match,
    overdue_days, overdue_bucket, status_code, load_ts
)
WITH
pod AS (
    SELECT *
    FROM (
        SELECT d.*,
               ROW_NUMBER() OVER (PARTITION BY d.distribution_id
                                  ORDER BY d.last_modification_date_time DESC) AS _rn
        FROM s2p.po_distribution AS d
    ) AS q
    WHERE q._rn = 1
),
poll AS (
    SELECT *
    FROM (
        SELECT l.*,
               ROW_NUMBER() OVER (PARTITION BY l.line_location_id
                                  ORDER BY l.last_modification_date_time DESC) AS _rn
        FROM s2p.po_line_location AS l
    ) AS q
    WHERE q._rn = 1
),
pol AS (
    SELECT pl.*
    FROM s2p.po_line AS pl
    WHERE pl.line_status <> 'CANCELLED'
),
poh AS (
    SELECT DISTINCT ON (h.po_header_id) h.*
    FROM s2p.po_header AS h
    ORDER BY h.po_header_id, h.last_modification_date_time DESC
),
pole AS (
    SELECT *
    FROM (
        SELECT e.*, ROW_NUMBER() OVER (PARTITION BY e.po_line_id ORDER BY 1) AS _rn
        FROM s2p.po_line_ext AS e
    ) AS q
    WHERE q._rn = 1
),
pohe AS (
    SELECT x.po_header_id, x.attribute1, x.attribute4, x.attribute12, x.attribute_number1
    FROM s2p.po_header_ext AS x
),
pov AS (
    SELECT v.po_header_id, v.approved_date, v.revision_num
    FROM (
        SELECT pv.*,
               ROW_NUMBER() OVER (
                   PARTITION BY pv.po_header_id
                   ORDER BY GREATEST(COALESCE(pv.submitted_date, TIMESTAMPTZ '1900-01-01'),
                                     COALESCE(pv.approved_date,  TIMESTAMPTZ '1900-01-01')) DESC
               ) AS _rn
        FROM s2p.po_version AS pv
    ) AS v
    WHERE v._rn = 1
),
pu AS (
    SELECT p.person_id, p.username, p.full_name
    FROM s2p.person AS p
    WHERE p.active_flag = 'Y'
),
cte_dedup AS (
    SELECT
        pod.distribution_id,
        pod.quantity_ordered,
        pod.quantity_cancelled,
        pod.amount_ordered,
        pod.amount_cancelled,
        poll.price_override,
        poll.need_by_date,
        poll.promised_date,
        poll.receipt_required_flag,
        poll.inspection_required_flag,
        pol.po_line_id,
        pol.line_number,
        pol.category_id,
        pol.uom_code,
        pol.unit_price                  AS line_unit_price,
        pol.quantity                    AS line_quantity,
        pol.amount                      AS line_amount,
        pol.matching_basis,
        pol.purchase_basis,
        pol.item_description,
        pol.line_status,
        poh.po_header_id,
        poh.segment1,
        poh.vendor_id,
        poh.agent_id,
        poh.currency_code,
        poh.document_status,
        poh.created_at                  AS hdr_created_at,
        pole.attribute1                 AS line_attribute1,
        pole.attribute4                 AS line_attribute4,
        pohe.attribute4                 AS hdr_attribute4,
        bpa_hdr.agent_id                AS bpa_agent_id,
        bpa_line.amount                 AS bpa_line_amount,
        bpa_line_ext.attribute1         AS bpa_line_attribute1,
        contract_hdr_ext.attribute4     AS contract_attribute4
    FROM pod
    JOIN poll ON poll.line_location_id = pod.line_location_id
    JOIN pol  ON pol.po_line_id = poll.po_line_id
    JOIN poh  ON poh.po_header_id = pol.po_header_id
    LEFT JOIN pole                     ON pole.po_line_id = pol.po_line_id
    LEFT JOIN pohe                     ON pohe.po_header_id = poh.po_header_id
    LEFT JOIN poh  AS bpa_hdr          ON bpa_hdr.po_header_id = pol.from_header_id
                                      AND bpa_hdr.type_lookup_code = 'BLANKET'
    LEFT JOIN poh  AS contract_hdr     ON contract_hdr.po_header_id = pol.contract_id
    LEFT JOIN pohe AS contract_hdr_ext ON contract_hdr_ext.po_header_id = contract_hdr.po_header_id
    LEFT JOIN pol  AS bpa_line         ON bpa_line.po_line_id = pol.from_line_id
    LEFT JOIN pole AS bpa_line_ext     ON bpa_line_ext.po_line_id = bpa_line.po_line_id
),
cte_reference_data AS (
    SELECT
        src.distribution_id,
        c.category_name,
        pc.category_name                AS parent_category_name,
        u.uom_name,
        pu.person_id                    AS buyer_person_id
    FROM cte_dedup AS src
    LEFT JOIN s2p.item_category AS c  ON c.category_id = src.category_id
    LEFT JOIN s2p.item_category AS pc ON pc.category_id = c.parent_category_id
    LEFT JOIN s2p.uom           AS u  ON u.uom_code = src.uom_code
    LEFT JOIN pu                      ON pu.person_id = COALESCE(src.agent_id, src.bpa_agent_id)
),
cte_starting_price AS (
    SELECT
        src.distribution_id,
        CASE
            WHEN COALESCE(src.line_attribute1, src.bpa_line_attribute1) ~ '^[.][0-9]+$'
                THEN regexp_replace(COALESCE(src.line_attribute1, src.bpa_line_attribute1), '\.', '0.')
            WHEN replace(COALESCE(src.line_attribute1, src.bpa_line_attribute1), ',', '') ~ '^[0-9]+([.][0-9]+)?$'
                THEN replace(COALESCE(src.line_attribute1, src.bpa_line_attribute1), ',', '')
            ELSE '0'
        END AS x_strtng_price_val,
        CASE
            WHEN src.bpa_line_attribute1 ~ '^[0-9]+([.][0-9]+)?$' THEN src.bpa_line_attribute1
            ELSE '0'
        END AS bpa_strng_price
    FROM cte_dedup AS src
),
cte_cost_avoidance AS (
    SELECT
        src.distribution_id,
        CASE
            WHEN src.line_attribute4 ~ '^[.][0-9]+$'
                THEN regexp_replace(src.line_attribute4, '\.', '0.')::numeric
            WHEN replace(src.line_attribute4, ',', '') ~ '^[0-9]+([.][0-9]+)?$'
                THEN replace(src.line_attribute4, ',', '')::numeric
            ELSE 0
        END AS cost_avoidance_amt
    FROM cte_dedup AS src
),
cte_amounts AS (
    SELECT
        src.distribution_id,
        src.purchase_basis,
        src.line_unit_price,
        src.bpa_line_amount,
        CASE
            WHEN src.matching_basis = 'AMOUNT'
                THEN src.amount_ordered - COALESCE(src.amount_cancelled, 0)
            ELSE (COALESCE(src.quantity_ordered, 0) - COALESCE(src.quantity_cancelled, 0))
                 * COALESCE(src.price_override, 0)
        END AS line_amt,
        CASE
            WHEN src.line_amount IS NOT NULL THEN src.line_amount
            ELSE src.line_unit_price * src.line_quantity
        END AS x_po_line_amt,
        COALESCE(src.quantity_ordered, 0) - COALESCE(src.quantity_cancelled, 0) AS dist_qty
    FROM cte_dedup AS src
),
cte_savings_base AS (
    SELECT
        a.distribution_id,
        a.purchase_basis,
        a.line_unit_price,
        a.bpa_line_amount,
        a.line_amt,
        a.x_po_line_amt,
        a.dist_qty,
        COALESCE(sp.bpa_strng_price::numeric, 0)    AS bpa_strng_price,
        COALESCE(sp.x_strtng_price_val::numeric, 0) AS strng_price
    FROM cte_amounts AS a
    LEFT JOIN cte_starting_price AS sp ON sp.distribution_id = a.distribution_id
),
cte_should_cost AS (
    SELECT
        b.*,
        CASE
            WHEN b.purchase_basis = 'QUANTITY' AND b.bpa_strng_price <> 0 THEN b.dist_qty * b.bpa_strng_price
            WHEN b.purchase_basis = 'QUANTITY' AND b.strng_price <> 0     THEN b.dist_qty * b.strng_price
            WHEN b.purchase_basis = 'QUANTITY'                            THEN b.dist_qty * b.line_unit_price
            WHEN b.purchase_basis = 'FIXED PRICE' AND b.bpa_strng_price <> 0 AND b.x_po_line_amt <> 0
                THEN (b.bpa_strng_price / NULLIF(b.bpa_line_amount, 0)) * b.line_amt
            WHEN b.purchase_basis = 'FIXED PRICE'                         THEN b.line_amt
        END AS should_cost,
        CASE
            WHEN b.purchase_basis = 'QUANTITY' AND b.strng_price <> 0     THEN b.dist_qty * b.strng_price
            WHEN b.purchase_basis = 'QUANTITY' AND b.bpa_strng_price <> 0 THEN b.dist_qty * b.bpa_strng_price
            WHEN b.purchase_basis = 'QUANTITY'
                 AND b.bpa_strng_price = 0 AND b.strng_price = 0          THEN b.dist_qty * b.line_unit_price
            WHEN b.purchase_basis = 'FIXED PRICE' AND b.strng_price <> 0 AND b.x_po_line_amt <> 0
                THEN b.strng_price * (b.line_amt / b.x_po_line_amt)
            WHEN b.purchase_basis = 'FIXED PRICE' AND b.bpa_strng_price <> 0 AND b.x_po_line_amt <> 0
                THEN (b.bpa_strng_price / NULLIF(b.bpa_line_amount, 0)) * b.line_amt
            WHEN b.purchase_basis = 'FIXED PRICE'                         THEN b.line_amt
        END AS should_cost_alt
    FROM cte_savings_base AS b
),
cte_overdue AS (
    SELECT
        src.distribution_id,
        CASE
            WHEN upper(src.line_status) IN ('OPEN', 'PENDING APPROVAL', 'PENDING ACKNOWLEDGMENT')
                THEN CURRENT_DATE
                     - COALESCE(src.promised_date, src.need_by_date, pov.approved_date,
                                src.hdr_created_at)::date
            ELSE 0
        END AS overdue_days
    FROM cte_dedup AS src
    LEFT JOIN pov ON pov.po_header_id = src.po_header_id
),
cte_final AS (
    SELECT
        d.distribution_id,
        d.po_header_id,
        d.segment1,
        d.line_number,
        d.vendor_id,
        d.agent_id,
        d.bpa_agent_id,
        d.item_description,
        d.document_status,
        d.currency_code,
        d.hdr_created_at,
        d.inspection_required_flag,
        d.receipt_required_flag,
        sc.line_amt,
        sc.dist_qty,
        COALESCE(sc.should_cost, sc.should_cost_alt) AS should_cost,
        ref.category_name,
        ref.parent_category_name,
        ref.uom_name,
        ref.buyer_person_id,
        ca.cost_avoidance_amt,
        od.overdue_days
    FROM cte_should_cost AS sc
    JOIN cte_dedup               AS d   ON d.distribution_id = sc.distribution_id
    LEFT JOIN cte_reference_data AS ref ON ref.distribution_id = d.distribution_id
    LEFT JOIN cte_cost_avoidance AS ca  ON ca.distribution_id = d.distribution_id
    JOIN cte_overdue             AS od  ON od.distribution_id = d.distribution_id
)
SELECT
    'PO~' || fin.distribution_id::text                                       AS integration_id,
    fin.po_header_id,
    fin.segment1                                                             AS po_number,
    fin.line_number::int                                                     AS line_number,
    fin.vendor_id                                                            AS supplier_id,
    COALESCE(fin.agent_id, fin.bpa_agent_id, fin.buyer_person_id)            AS buyer_id,
    concat_ws(' | ', fin.parent_category_name, fin.category_name,
              regexp_replace(fin.item_description, '[\r\n]+', ' ', 'g'),
              fin.uom_name)                                                  AS item_description,
    fin.line_amt,
    fin.dist_qty                                                             AS po_dist_qty,
    fin.should_cost,
    (COALESCE(fin.should_cost, 0) - fin.line_amt + COALESCE(fin.cost_avoidance_amt, 0))
        * COALESCE(fx.conversion_rate, 1)                                    AS savings_amt,
    CASE
        WHEN fin.inspection_required_flag = 'N' AND fin.receipt_required_flag = 'N' THEN '2-WAY'
        WHEN fin.inspection_required_flag = 'N' AND fin.receipt_required_flag = 'Y' THEN '3-WAY'
        WHEN fin.inspection_required_flag = 'Y' AND fin.receipt_required_flag = 'Y' THEN '4-WAY'
    END                                                                      AS x_po_match,
    fin.overdue_days,
    CASE
        WHEN fin.overdue_days <= 0              THEN 'No Overdue'
        WHEN fin.overdue_days BETWEEN 1 AND 30  THEN '1-30 Days'
        WHEN fin.overdue_days BETWEEN 31 AND 90 THEN '31-90 Days'
        ELSE '90+ Days'
    END                                                                      AS overdue_bucket,
    ds.status_code,
    now()                                                                    AS load_ts
FROM cte_final AS fin
LEFT JOIN LATERAL (
    SELECT r.conversion_rate
    FROM gold.fx_rate AS r
    WHERE r.from_ccy = fin.currency_code
      AND r.to_ccy = 'USD'
      AND r.rate_date <= fin.hdr_created_at::date
    ORDER BY r.rate_date DESC
    LIMIT 1
) AS fx ON TRUE
LEFT JOIN gold.dim_status AS ds
       ON ds.integration_id = 'PURCHASE_ORDER_STATUS~' || fin.document_status
      AND ds.datasource_num_id = 100
WHERE fin.document_status IN ('OPEN', 'APPROVED', 'CLOSED')
  AND fin.line_amt <> 0
ON CONFLICT (integration_id) DO UPDATE
SET line_amt     = EXCLUDED.line_amt,
    should_cost  = EXCLUDED.should_cost,
    savings_amt  = EXCLUDED.savings_amt,
    overdue_days = GREATEST(f.overdue_days, EXCLUDED.overdue_days),
    status_code  = EXCLUDED.status_code,
    load_ts      = EXCLUDED.load_ts
WHERE f.status_code IS DISTINCT FROM EXCLUDED.status_code
   OR f.line_amt <> EXCLUDED.line_amt;

-- -----------------------------------------------------------------------------------------
-- S2 · summarise the fact into a new table
-- -----------------------------------------------------------------------------------------
CREATE TABLE mart.po_supplier_summary AS
SELECT f.supplier_id,
       s.vendor_name                                         AS supplier_name,
       COUNT(DISTINCT f.po_header_id)                        AS po_count,
       SUM(f.line_amt)                                       AS total_spend,
       SUM(f.savings_amt) FILTER (WHERE f.savings_amt > 0)   AS total_savings,
       RANK() OVER (ORDER BY SUM(f.line_amt) DESC)           AS spend_rank
FROM mart.fact_po_line AS f
JOIN s2p.supplier      AS s ON s.vendor_id = f.supplier_id
WHERE f.status_code IS NOT NULL
GROUP BY f.supplier_id, s.vendor_name;

-- -----------------------------------------------------------------------------------------
-- S3 · load the mart from the table S2 created
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.supplier_spend
    (supplier_id, supplier_name, spend_year, po_count, total_spend, total_savings, spend_rank)
SELECT ps.supplier_id,
       ps.supplier_name,
       EXTRACT(YEAR FROM CURRENT_DATE)::int,
       ps.po_count,
       ps.total_spend,
       ps.total_savings,
       ps.spend_rank
FROM mart.po_supplier_summary AS ps
WHERE ps.spend_rank <= 100;

-- -----------------------------------------------------------------------------------------
-- S4 · age open lines by their requisition's need-by date
-- -----------------------------------------------------------------------------------------
WITH
req AS (
    SELECT rd.req_distribution_id,
           rh.approved_date         AS req_approved_date,
           rl.need_by_date          AS req_need_by_date,
           ROW_NUMBER() OVER (PARTITION BY rd.req_distribution_id
                              ORDER BY rd.last_modification_date_time DESC) AS rn
    FROM s2p.req_distribution AS rd
    JOIN s2p.req_line         AS rl ON rl.req_line_id = rd.req_line_id
    JOIN s2p.req_header       AS rh ON rh.req_header_id = rl.req_header_id
    WHERE rd.distribution_quantity > 0
),
po_req AS (
    SELECT 'PO~' || d.distribution_id::text AS integration_id,
           r.req_approved_date,
           r.req_need_by_date
    FROM s2p.po_distribution AS d
    JOIN req AS r ON r.req_distribution_id = d.req_distribution_id
                 AND r.rn = 1
)
UPDATE mart.fact_po_line AS f
SET overdue_days   = GREATEST(f.overdue_days, CURRENT_DATE - pr.req_need_by_date::date),
    overdue_bucket = CASE
                         WHEN CURRENT_DATE - pr.req_need_by_date::date > 90 THEN '90+ Days'
                         ELSE f.overdue_bucket
                     END
FROM po_req AS pr
WHERE pr.integration_id = f.integration_id
  AND pr.req_approved_date IS NOT NULL;

-- -----------------------------------------------------------------------------------------
-- S5 · drop fully cancelled distributions
-- -----------------------------------------------------------------------------------------
DELETE FROM mart.fact_po_line AS f
USING s2p.po_distribution AS d
WHERE f.integration_id = 'PO~' || d.distribution_id::text
  AND d.quantity_cancelled >= d.quantity_ordered;

-- -----------------------------------------------------------------------------------------
-- S6 · reconcile this year's spend from the summary
-- -----------------------------------------------------------------------------------------
MERGE INTO mart.supplier_spend AS t
USING (
    SELECT ps.supplier_id, ps.supplier_name, ps.total_spend, ps.spend_rank
    FROM mart.po_supplier_summary AS ps
    WHERE ps.po_count > 0
) AS s
ON t.supplier_id = s.supplier_id
WHEN MATCHED AND s.total_spend IS DISTINCT FROM t.total_spend THEN
    UPDATE SET total_spend = s.total_spend,
               spend_rank  = s.spend_rank
WHEN NOT MATCHED THEN
    INSERT (supplier_id, supplier_name, spend_year, total_spend, spend_rank)
    VALUES (s.supplier_id, s.supplier_name, EXTRACT(YEAR FROM CURRENT_DATE)::int,
            s.total_spend, s.spend_rank)
WHEN NOT MATCHED BY SOURCE AND t.spend_year = EXTRACT(YEAR FROM CURRENT_DATE)::int THEN
    DELETE;

-- -----------------------------------------------------------------------------------------
-- S7 · validation query
-- -----------------------------------------------------------------------------------------
SELECT f.status_code,
       f.x_po_match,
       GROUPING(f.status_code, f.x_po_match)                    AS grp,
       COUNT(*)                                                 AS lines,
       SUM(f.line_amt)                                          AS line_amt,
       SUM(f.savings_amt) FILTER (WHERE f.overdue_days > 30)    AS overdue_savings,
       AVG(f.overdue_days)                                      AS avg_overdue
FROM mart.fact_po_line AS f
WHERE f.load_ts >= CURRENT_DATE - 1
GROUP BY GROUPING SETS ((f.status_code, f.x_po_match), (f.status_code), ())
HAVING COUNT(*) > 0
ORDER BY f.status_code NULLS LAST, f.x_po_match NULLS LAST;

-- -----------------------------------------------------------------------------------------
-- S8 · yearly history: the fact joined back to its source header
-- -----------------------------------------------------------------------------------------
INSERT INTO mart.supplier_spend
    (supplier_id, supplier_name, spend_year, po_count, total_spend, total_savings, spend_rank)
WITH
yearly AS (
    SELECT f.supplier_id,
           EXTRACT(YEAR FROM h.created_at)::int     AS spend_year,
           COUNT(DISTINCT f.po_header_id)           AS po_count,
           SUM(f.line_amt)                          AS total_spend,
           SUM(GREATEST(f.savings_amt, 0))          AS total_savings
    FROM mart.fact_po_line AS f
    JOIN s2p.po_header     AS h ON h.po_header_id = f.po_header_id
    WHERE h.document_status <> 'CANCELLED'
    GROUP BY f.supplier_id, EXTRACT(YEAR FROM h.created_at)
),
ranked AS (
    SELECT y.*,
           DENSE_RANK() OVER (PARTITION BY y.spend_year ORDER BY y.total_spend DESC) AS spend_rank
    FROM yearly AS y
)
SELECT r.supplier_id,
       s.vendor_name,
       r.spend_year,
       r.po_count,
       r.total_spend,
       r.total_savings,
       r.spend_rank
FROM ranked AS r
LEFT JOIN s2p.supplier AS s ON s.vendor_id = r.supplier_id
WHERE r.spend_year < EXTRACT(YEAR FROM CURRENT_DATE)
ON CONFLICT DO NOTHING;
