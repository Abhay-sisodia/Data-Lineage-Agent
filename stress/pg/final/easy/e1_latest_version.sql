-- =========================================================================================
-- E1 · latest version per key (easy)                       final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: the purchase-order sample's dedup CTEs (pod / poll / pol / poh / pov / pode),
-- joined into one bare SELECT. PostgreSQL 17.
--   * ROW_NUMBER() = 1 over `SELECT x.*` on the inner spine ......... D-9  filters
--   * the same dedup with a NAMED window ............................ D-9  filters
--   * DISTINCT ON with tie-breakers, WHERE beside it ................. D-9  filters
--   * a ROW_NUMBER dedup that is LEFT JOINed (null side) ............. D-14 / D-45 influence
--   * a window ORDER BY a literal (`ORDER BY 1` is not positional) ... nothing
--   * a GROUP BY version summary that is LEFT JOINed ................. D-2  influence
--   * COALESCE / GREATEST / CASE over one or two columns ............. D-19
--   * join keys .................................................... D-5  structural
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
pod AS (
    SELECT *
    FROM (
        SELECT d.*,
               ROW_NUMBER() OVER (
                   PARTITION BY d.distribution_id
                   ORDER BY d.last_modification_date_time DESC
               ) AS _rn
        FROM s2p.po_distribution AS d
    ) AS q
    WHERE q._rn = 1
),
poll AS (
    SELECT l.*
    FROM (
        SELECT ll.*, ROW_NUMBER() OVER w AS _rn
        FROM s2p.po_line_location AS ll
        WINDOW w AS (PARTITION BY ll.line_location_id ORDER BY ll.last_modification_date_time DESC)
    ) AS l
    WHERE l._rn = 1
),
pol AS (
    SELECT pl.po_line_id, pl.po_header_id, pl.line_number, pl.unit_price
    FROM s2p.po_line AS pl
    WHERE pl.line_status IS DISTINCT FROM 'CANCELLED'
),
poh AS (
    SELECT DISTINCT ON (h.po_header_id)
           h.po_header_id, h.segment1, h.type_lookup_code, h.vendor_id
    FROM s2p.po_header AS h
    WHERE h.document_status <> 'INCOMPLETE'
    ORDER BY h.po_header_id, h.last_modification_date_time DESC NULLS LAST, h.created_at DESC
),
pole AS (
    SELECT x.po_line_id, x.attribute1, x.attribute12
    FROM (
        SELECT e.*, ROW_NUMBER() OVER (PARTITION BY e.po_line_id ORDER BY 1) AS _rn
        FROM s2p.po_line_ext AS e
    ) AS x
    WHERE x._rn = 1
),
pov AS (
    SELECT v.po_header_id, v.revision_num, v.change_order_description
    FROM (
        SELECT pv.*,
               ROW_NUMBER() OVER (
                   PARTITION BY pv.po_header_id
                   ORDER BY GREATEST(COALESCE(pv.submitted_date, TIMESTAMPTZ '1900-01-01'),
                                     COALESCE(pv.approved_date, TIMESTAMPTZ '1900-01-01')) DESC
               ) AS _rn
        FROM s2p.po_version AS pv
    ) AS v
    WHERE v._rn = 1
),
pov_sub AS (
    SELECT pv.po_header_id,
           MIN(pv.submitted_date) AS first_submitted,
           COUNT(*)               AS version_count
    FROM s2p.po_version AS pv
    GROUP BY pv.po_header_id
)
SELECT
    pod.distribution_id                                               AS integration_id,
    poh.segment1                                                      AS po_number,
    CAST(pol.line_number AS integer)                                  AS line_number,
    pod.distribution_num,
    poll.shipment_num::text                                           AS schedule_number,
    poh.vendor_id                                                     AS supplier_id,
    COALESCE(pod.quantity_ordered, 0) - COALESCE(pod.quantity_cancelled, 0) AS po_dist_qty,
    COALESCE(poll.price_override, pol.unit_price)                     AS unit_price,
    pole.attribute12                                                  AS should_cost_flag,
    COALESCE(pole.attribute1, '0')                                    AS starting_price_txt,
    pov.revision_num                                                  AS latest_revision,
    pov.change_order_description                                      AS change_order_desc,
    ps.first_submitted                                                AS po_submit_on_dt,
    ps.version_count,
    GREATEST(pod.last_modification_date_time, poll.last_modification_date_time) AS aux1_changed_on_dt,
    CASE WHEN poh.type_lookup_code = 'BLANKET' THEN poh.segment1 END  AS blanket_agreement_number,
    'N'::char(1)                                                      AS deleted_flag,
    CURRENT_TIMESTAMP                                                 AS load_ts
FROM pod
JOIN poll            ON poll.line_location_id = pod.line_location_id
JOIN pol             ON pol.po_line_id = poll.po_line_id
JOIN poh             ON poh.po_header_id = pol.po_header_id
LEFT JOIN pole       ON pole.po_line_id = pol.po_line_id
LEFT JOIN pov        ON pov.po_header_id = poh.po_header_id
LEFT JOIN pov_sub AS ps ON ps.po_header_id = poh.po_header_id
WHERE pod.quantity_ordered > 0
ORDER BY integration_id;
