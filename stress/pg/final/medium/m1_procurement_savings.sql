-- =========================================================================================
-- M1 · procurement savings chain (medium)                   final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: the purchase-order sample's cte_dedup -> starting price -> amounts -> should cost
-- -> savings chain, translated to PostgreSQL, one bare SELECT.
--   * dedup CTEs on the inner spine (ROW_NUMBER, DISTINCT ON over h.*) ... D-9 filters
--   * the SAME spine CTEs re-joined on the null side (poh as bpa_hdr and
--     contract_hdr, pol as bpa_line): their own filters stay filters
--     only, and add nothing to the columns they feed there ................ D-45 exception
--   * a null-side dedup read twice (pole, pole AS bpa_line_ext) ............ D-45 influence
--   * a one-relation ON condition on a null side (bpa_hdr BLANKET) ......... D-48
--   * a sibling CTE of the spine LEFT JOINed back by key ................... D-45 exception
--   * CASE over CASE over COALESCE, five levels deep ....................... D-19, D-51
--   * COALESCE over two instances of ONE base column ....................... D-19 (conditional)
--   * a LEFT-joined CTE with its own LEFT joins and WHERE .................. D-45 values
--   * a dimension LEFT join with a literal ON test ......................... D-48
--   * window aggregates partitioned by header .............................. D-4
--   * WHERE over a CASE-derived amount ..................................... filters, every operand
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
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
cte_dedup AS (
    SELECT
        pod.distribution_id,
        pod.quantity_ordered,
        pod.quantity_cancelled,
        pod.amount_ordered,
        pod.amount_cancelled,
        pod.req_distribution_id,
        poll.price_override,
        poll.need_by_date,
        poll.promised_date,
        poll.receipt_required_flag,
        poll.inspection_required_flag,
        pol.po_line_id,
        pol.line_number,
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
        poh.document_status,
        pole.attribute1                 AS line_attribute1,
        pole.attribute12                AS line_attribute12,
        pohe.attribute4                 AS hdr_attribute4,
        pohe.attribute_number1          AS hdr_attribute_number1,
        bpa_hdr.agent_id                AS bpa_agent_id,
        bpa_hdr_ext.attribute4          AS bpa_hdr_attribute4,
        bpa_line.amount                 AS bpa_line_amount,
        bpa_line_ext.attribute1         AS bpa_line_attribute1,
        bpa_line_ext.attribute12        AS bpa_line_attribute12,
        contract_hdr_ext.attribute4     AS contract_attribute4
    FROM pod
    JOIN poll ON poll.line_location_id = pod.line_location_id
    JOIN pol  ON pol.po_line_id = poll.po_line_id
    JOIN poh  ON poh.po_header_id = pol.po_header_id
    LEFT JOIN pole                     ON pole.po_line_id = pol.po_line_id
    LEFT JOIN pohe                     ON pohe.po_header_id = poh.po_header_id
    LEFT JOIN poh  AS bpa_hdr          ON bpa_hdr.po_header_id = pol.from_header_id
                                      AND bpa_hdr.type_lookup_code = 'BLANKET'
    LEFT JOIN pohe AS bpa_hdr_ext      ON bpa_hdr_ext.po_header_id = pol.from_header_id
    LEFT JOIN poh  AS contract_hdr     ON contract_hdr.po_header_id = pol.contract_id
    LEFT JOIN pohe AS contract_hdr_ext ON contract_hdr_ext.po_header_id = contract_hdr.po_header_id
    LEFT JOIN pol  AS bpa_line         ON bpa_line.po_line_id = pol.from_line_id
    LEFT JOIN pole AS bpa_line_ext     ON bpa_line_ext.po_line_id = bpa_line.po_line_id
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
        COALESCE(src.quantity_ordered, 0) - COALESCE(src.quantity_cancelled, 0) AS dist_qty,
        CASE
            WHEN upper(src.line_status) IN ('OPEN', 'PENDING APPROVAL')
                THEN CURRENT_DATE - COALESCE(src.promised_date, src.need_by_date)::date
            ELSE 0
        END AS overdue_days
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
        a.overdue_days,
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
        END AS should_cost
    FROM cte_savings_base AS b
),
cte_savings AS (
    SELECT
        c.*,
        COALESCE(c.should_cost, 0) - c.line_amt AS savings_amt,
        CASE
            WHEN COALESCE(c.should_cost, 0) <> 0
                THEN (COALESCE(c.should_cost, 0) - c.line_amt) / c.should_cost
            ELSE 0
        END AS savings_pct,
        GREATEST(COALESCE(c.should_cost, 0) - c.line_amt, 0) AS zerorized_savings
    FROM cte_should_cost AS c
),
cte_requisition AS (
    SELECT rd.req_distribution_id,
           rh.requisition_number,
           rh.approved_date AS req_approved_date
    FROM s2p.req_distribution AS rd
    LEFT JOIN s2p.req_line   AS rl ON rl.req_line_id = rd.req_line_id
    LEFT JOIN s2p.req_header AS rh ON rh.req_header_id = rl.req_header_id
    WHERE rd.distribution_quantity > 0
)
SELECT
    'PO~' || d.distribution_id::text                                   AS integration_id,
    d.segment1                                                         AS po_number,
    d.line_number::int                                                 AS line_number,
    d.vendor_id                                                        AS supplier_id,
    COALESCE(d.agent_id, d.bpa_agent_id)                               AS buyer_id,
    regexp_replace(d.item_description, '[\r\n]+', ' ', 'g')            AS item_description,
    sv.line_amt,
    sv.dist_qty                                                        AS po_dist_qty,
    sv.should_cost,
    sv.savings_amt,
    sv.savings_pct,
    sv.zerorized_savings,
    substr(COALESCE(d.line_attribute12, d.hdr_attribute4, d.bpa_line_attribute12,
                    d.bpa_hdr_attribute4, d.contract_attribute4), 1, 15) AS x_should_cost_avbl,
    CASE
        WHEN d.line_attribute12 IS NOT NULL     THEN 'POL'
        WHEN d.hdr_attribute4 IS NOT NULL       THEN 'POH'
        WHEN d.bpa_line_attribute12 IS NOT NULL THEN 'BPAL'
        WHEN d.bpa_hdr_attribute4 IS NOT NULL   THEN 'BPAH'
        WHEN d.contract_attribute4 IS NOT NULL  THEN 'CPAH'
    END                                                                AS x_should_cost_level,
    CASE
        WHEN d.inspection_required_flag = 'N' AND d.receipt_required_flag = 'N' THEN '2-WAY'
        WHEN d.inspection_required_flag = 'N' AND d.receipt_required_flag = 'Y' THEN '3-WAY'
        WHEN d.inspection_required_flag = 'Y' AND d.receipt_required_flag = 'Y' THEN '4-WAY'
    END                                                                AS x_po_match,
    sv.overdue_days,
    CASE
        WHEN sv.overdue_days <= 0               THEN 'No Overdue'
        WHEN sv.overdue_days BETWEEN 1 AND 30   THEN '1-30 Days'
        WHEN sv.overdue_days BETWEEN 31 AND 90  THEN '31-90 Days'
        ELSE '90+ Days'
    END                                                                AS overdue_bucket,
    d.hdr_attribute_number1::numeric                                   AS x_cpa_discount,
    req.requisition_number                                             AS purch_requisition_number,
    req.req_approved_date,
    ds.status_name                                                     AS po_status_name,
    dsup.supplier_name,
    SUM(sv.line_amt) OVER (PARTITION BY d.po_header_id)                AS po_total_amt,
    sv.line_amt / NULLIF(SUM(sv.line_amt) OVER (PARTITION BY d.po_header_id), 0) AS share_of_po,
    COUNT(*) OVER (PARTITION BY d.po_header_id)                        AS lines_in_po,
    CURRENT_TIMESTAMP                                                  AS load_ts
FROM cte_savings AS sv
JOIN cte_dedup   AS d ON d.distribution_id = sv.distribution_id
LEFT JOIN cte_requisition AS req ON req.req_distribution_id = d.req_distribution_id
LEFT JOIN gold.dim_status AS ds
       ON ds.integration_id = 'PURCHASE_ORDER_STATUS~' || d.document_status
      AND ds.datasource_num_id = 100
LEFT JOIN gold.dim_supplier AS dsup ON dsup.integration_id = d.vendor_id::text
WHERE d.document_status IN ('OPEN', 'APPROVED', 'CLOSED')
  AND sv.line_amt <> 0;
