-- =========================================================================================
-- E4 · item and shipment expressions (easy)                 final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: an item feed normalised by scalar expressions, joined to shipped lines, one bare
-- SELECT. PostgreSQL 17.
--   * string functions, SUBSTRING FROM FOR, split_part, || ........... derived
--   * CASE / COALESCE / NULLIF / GREATEST / LEAST over 1 or 2 columns . D-19
--   * casts (CAST, ::), date_trunc, to_char, EXTRACT, interval math ... derived
--   * jsonb ->>, #>>, ?, @> ; arrays: subscript, cardinality, ARRAY[] . D-29 derived
--   * jsonb_build_object, md5 over a concatenation .................... derived
--   * a call to a function with no body in the analysis .............. D-53 derived + boundary
--   * WHERE: IN, BETWEEN, SIMILAR TO, IS DISTINCT FROM, NaN test ...... filters
--   * literal-only and NULL-typed columns ............................. nothing
-- Expected: one boundary, source_unavailable NORMALISE_UOM (D-53). No refusal.
-- =========================================================================================
WITH
items AS (
    SELECT im.item_id, im.item_code, im.item_desc, im.item_class, im.std_cost,
           im.list_price, im.weight_kg, im.attrs, im.tags, im.created_at
    FROM lgx.item_master AS im
),
priced AS (
    SELECT i.item_id,
           upper(btrim(i.item_code))                                     AS code_norm,
           SUBSTRING(i.item_code FROM 1 FOR 3)                           AS code_prefix,
           split_part(i.item_code, '-', 2)                               AS code_suffix,
           COALESCE(NULLIF(btrim(i.item_desc), ''), i.item_code)         AS display_name,
           CASE
               WHEN i.list_price IS NULL               THEN 'NO_PRICE'
               WHEN i.list_price < i.std_cost          THEN 'BELOW_COST'
               WHEN i.list_price < i.std_cost * 1.2    THEN 'THIN'
               ELSE 'HEALTHY'
           END                                                           AS margin_band,
           ROUND((i.list_price - i.std_cost) / NULLIF(i.list_price, 0) * 100, 2) AS margin_pct,
           CASE i.item_class WHEN 'SERVICE' THEN 0 ELSE i.weight_kg END  AS ship_weight,
           i.attrs ->> 'hazmat_class'                                    AS hazmat_class,
           (i.attrs #>> '{dims,length_cm}')::numeric                     AS length_cm,
           COALESCE((i.attrs ->> 'shelf_life_days')::int, 0)             AS shelf_life_days,
           i.attrs ? 'serialised'                                        AS is_serialised,
           cardinality(i.tags)                                           AS tag_count,
           i.tags[1]                                                     AS primary_tag,
           'ITEM~' || i.item_id::text                                    AS integration_id,
           to_char(i.created_at, 'YYYY-MM')                              AS created_month,
           date_trunc('month', i.created_at)::date                       AS created_month_start,
           EXTRACT(EPOCH FROM (now() - i.created_at)) / 86400            AS age_days,
           util.normalise_uom(i.item_code, i.weight_kg)                  AS uom_key
    FROM items AS i
    WHERE i.item_class IS DISTINCT FROM 'OBSOLETE'
)
SELECT
    p.integration_id,
    p.code_norm,
    p.display_name,
    p.margin_band,
    p.margin_pct,
    p.ship_weight,
    p.hazmat_class,
    p.length_cm,
    p.shelf_life_days,
    p.is_serialised,
    p.tag_count,
    p.primary_tag,
    p.created_month,
    p.created_month_start,
    p.age_days,
    p.uom_key,
    ARRAY[p.code_prefix, p.code_suffix]                                  AS code_parts,
    sl.shipped_qty * sl.unit_selling_price                               AS ext_amount,
    GREATEST(sl.ship_date, sl.promise_date)                              AS latest_date,
    LEAST(sl.ordered_qty, sl.shipped_qty)                                AS capped_qty,
    sl.ship_date - sl.promise_date                                       AS days_late,
    sl.ship_date > sl.promise_date                                       AS is_late,
    sl.line_attrs @> '{"rush": true}'                                    AS is_rush,
    NULLIF(sl.cancelled_qty, 0)                                          AS cancelled_qty,
    jsonb_build_object('code', p.code_norm, 'qty', sl.shipped_qty)       AS payload,
    md5(p.integration_id || '|' || sl.shipment_line_id::text)            AS row_hash,
    'v1'::text                                                           AS schema_version,
    NULL::numeric                                                        AS reserved_amount
FROM priced AS p
JOIN lgx.shipment_line AS sl ON sl.item_id = p.item_id
WHERE sl.line_status IN ('SHIPPED', 'INVOICED')
  AND sl.shipped_qty BETWEEN 1 AND 100000
  AND sl.line_type_code SIMILAR TO '(STD|KIT)%'
  AND (sl.unit_selling_price IS NULL OR sl.unit_selling_price <> 'NaN'::numeric);
