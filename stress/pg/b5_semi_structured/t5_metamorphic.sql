-- =====================================================================================
-- B5 · T5 · METAMORPHIC — do equivalent document spellings give identical lineage?
--
-- Objective card: stress/pg/b5_semi_structured/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P8 MUST BE EQUAL.
--
-- Then one realistic event-stream model: purchase events keyed by UTM source, joined to
-- customers' region taken from their attributes document, by month.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- P1 · ->> vs #>> over a single key
-- @pair P1 A
INSERT INTO mart.kpi_rollup (level)
SELECT o.payload ->> 'k'
FROM staging.orders AS o;

-- @pair P1 B
INSERT INTO mart.kpi_rollup (level)
SELECT o.payload #>> '{k}'
FROM staging.orders AS o;


-- P2 · jsonb_extract_path_text vs a chain of ->
-- @pair P2 A
INSERT INTO mart.customer_summary (region)
SELECT jsonb_extract_path_text(c.attributes, 'geo', 'region')
FROM staging.customers AS c;

-- @pair P2 B
INSERT INTO mart.customer_summary (region)
SELECT c.attributes -> 'geo' ->> 'region'
FROM staging.customers AS c;


-- P3 · -> vs a subscript
-- @pair P3 A
INSERT INTO mart.kpi_rollup (region)
SELECT (o.payload -> 'geo')::text
FROM staging.orders AS o;

-- @pair P3 B
INSERT INTO mart.kpi_rollup (region)
SELECT (o.payload['geo'])::text
FROM staging.orders AS o;


-- P4 · jsonb_array_elements_text in FROM vs in the select list
-- @pair P4 A
INSERT INTO mart.channel_mix (channel)
SELECT t.tag
FROM staging.orders AS o, jsonb_array_elements_text(o.payload -> 'tags') AS t(tag);

-- @pair P4 B
INSERT INTO mart.channel_mix (channel)
SELECT jsonb_array_elements_text(o.payload -> 'tags')
FROM staging.orders AS o;


-- P5 · JSON_TABLE vs jsonb_to_recordset
-- @pair P5 A
INSERT INTO mart.product_performance (units)
SELECT jt.qty
FROM staging.orders AS o,
     JSON_TABLE(o.payload, '$.items[*]' COLUMNS (qty bigint PATH '$.qty')) AS jt;

-- @pair P5 B
INSERT INTO mart.product_performance (units)
SELECT r.qty
FROM staging.orders AS o, jsonb_to_recordset(o.payload -> 'items') AS r(qty bigint);


-- P6 · containment vs key equality, in WHERE
-- @pair P6 A
INSERT INTO mart.channel_mix (region)
SELECT o.channel
FROM staging.orders AS o
WHERE o.payload @> '{"tier": "gold"}';

-- @pair P6 B
INSERT INTO mart.channel_mix (region)
SELECT o.channel
FROM staging.orders AS o
WHERE o.payload ->> 'tier' = 'gold';


-- P7 · jsonb_agg vs array_to_json(array_agg)
-- @pair P7 A
INSERT INTO mart.top_customers (region, full_name)
SELECT o.channel, jsonb_agg(o.amount)::text
FROM staging.orders AS o
GROUP BY o.channel;

-- @pair P7 B
INSERT INTO mart.top_customers (region, full_name)
SELECT o.channel, array_to_json(array_agg(o.amount))::text
FROM staging.orders AS o
GROUP BY o.channel;


-- P8 · hstore -> vs an hstore subscript
-- @pair P8 A
INSERT INTO mart.customer_summary (full_name)
SELECT d.attrs -> 'model'
FROM staging.device_attrs AS d;

-- @pair P8 B
INSERT INTO mart.customer_summary (full_name)
SELECT d.attrs['model']
FROM staging.device_attrs AS d;


-- -------------------------------------------------------------------------------------
-- R1 · purchase events by month, UTM source and customer region
--   events: purchase events that carry a UTM source; source and value from the document
--   final:  joined to customers, the region read from their attributes document,
--           grouped by month, source and region
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, channel, region, orders, revenue)
WITH events AS (
    SELECT
        w.customer_id,
        date_trunc('month', w.event_ts)::date AS m,
        w.properties ->> 'utm_source' AS src,
        (w.properties ->> 'value')::numeric AS val
    FROM staging.web_events AS w
    WHERE w.event_type = 'purchase'
      AND w.properties ? 'utm_source'
)
SELECT
    e.m,
    e.src,
    c.attributes ->> 'region',
    COUNT(DISTINCT e.customer_id),
    SUM(e.val)
FROM events AS e
JOIN staging.customers AS c ON c.id = e.customer_id
GROUP BY e.m, e.src, c.attributes ->> 'region';
