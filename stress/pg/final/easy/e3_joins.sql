-- =========================================================================================
-- E3 · shipment joins (easy)                                final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: a shipment line enriched from every join kind, one bare SELECT. PostgreSQL 17.
--   * INNER join keys, several in a chain ........................... D-5 / D-11 structural
--   * an INNER join with a one-relation ON condition ................. D-48 filter
--   * LEFT joins with one-relation ON conditions ..................... D-48 conditional value
--   * a LEFT join with a range (non-equality) ON ..................... D-49 / D-48 values
--   * a CTE whose WHERE is reached only through a LEFT join ........... D-45 values
--   * JOIN USING, NATURAL JOIN ....................................... structural
--   * RIGHT JOIN with a null-side ON condition, itself LEFT joined .... D-48
--   * FULL JOIN of two aggregates, keyed by COALESCE ................. D-2 influence
--   * a non-equality between two relations in WHERE .................. D-49 filter
--   * IN (subquery) and NOT EXISTS (subquery) ........................ D-52 filters
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
WITH
hdr AS (
    SELECT h.shipment_id, h.customer_id, h.carrier_code, h.ship_to_site_id,
           h.ship_from_org_id, h.status_code, h.created_at
    FROM lgx.shipment_header AS h
    WHERE h.is_deleted IS NOT TRUE
),
svc_mode AS (
    SELECT svc.carrier_code, svc.service_level, svc.transit_days, tm.mode_desc
    FROM lgx.carrier_service AS svc
    NATURAL JOIN lgx.transport_mode AS tm
),
cs AS (
    SELECT carrier_code, cr.carrier_name, sm.service_level, sm.transit_days, sm.mode_desc
    FROM lgx.carrier AS cr
    LEFT JOIN svc_mode AS sm USING (carrier_code)
    WHERE cr.active_flag = 'Y'
),
wh AS (
    SELECT w.org_id, w.warehouse_code, o.org_name
    FROM crm.organization AS o
    RIGHT JOIN lgx.warehouse AS w ON w.org_id = o.org_id AND o.is_active
),
plan_vs_ship AS (
    SELECT COALESCE(p.item_id, s.item_id) AS item_id, p.planned_qty, s.shipped_total
    FROM (SELECT b.parent_item_id AS item_id, SUM(b.qty_per) AS planned_qty
          FROM lgx.bill_of_material AS b
          GROUP BY b.parent_item_id) AS p
    FULL JOIN (SELECT l2.item_id, SUM(l2.shipped_qty) AS shipped_total
               FROM lgx.shipment_line AS l2
               GROUP BY l2.item_id) AS s
      ON s.item_id = p.item_id
)
SELECT
    ln.shipment_line_id,
    hdr.shipment_id,
    c.customer_name,
    cs.carrier_name,
    cs.service_level,
    cs.mode_desc,
    ln.ship_date + COALESCE(cs.transit_days, 0)          AS expected_delivery,
    site.site_code,
    org.org_name                                          AS ship_from_org,
    wh.warehouse_code,
    wh.org_name                                           AS warehouse_org,
    pvs.planned_qty,
    pvs.shipped_total,
    ln.shipped_qty
FROM hdr
JOIN lgx.shipment_line AS ln ON ln.shipment_id = hdr.shipment_id
JOIN crm.customer      AS c  ON c.customer_id = hdr.customer_id AND c.status = 'ACTIVE'
LEFT JOIN cs                 ON cs.carrier_code = hdr.carrier_code
LEFT JOIN crm.customer_site AS site
       ON site.site_id = hdr.ship_to_site_id
      AND site.time_zone_name IS NOT NULL
LEFT JOIN gold.dim_organization AS org
       ON org.integration_id = hdr.ship_from_org_id::text
      AND hdr.created_at::date >= org.organization_start_date
      AND hdr.created_at::date <  org.organization_end_date
LEFT JOIN wh                 ON wh.org_id = hdr.ship_from_org_id
LEFT JOIN plan_vs_ship AS pvs ON pvs.item_id = ln.item_id
WHERE hdr.status_code <> 'VOID'
  AND ln.ship_date >= hdr.created_at::date
  AND hdr.customer_id IN (SELECT ct.customer_id FROM crm.contact AS ct WHERE ct.is_primary)
  AND NOT EXISTS (SELECT 1
                  FROM lgx.package_event AS pe
                  WHERE pe.shipment_id = hdr.shipment_id
                    AND pe.event_type = 'VOID');
