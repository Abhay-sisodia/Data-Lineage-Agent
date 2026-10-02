-- =====================================================================================
-- B5 · T1 · BREADTH — is each semi-structured construct supported at all?
--
-- Objective card: stress/pg/b5_semi_structured/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql (with B5's xml and hstore tables). PostgreSQL: 17
--                 (JSON_TABLE 17; JSON_VALUE / JSON_QUERY 17; JSON_OBJECT, IS JSON 16;
--                 jsonb subscripts 14; hstore extension assumed).
--
-- One construct per statement. Conventions: every earlier decision, plus D-29 (lineage
-- into and out of a document is column-level) and D-30 (JSON_TABLE / XMLTABLE are
-- analysed). Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · extraction: ->>, -> then ->>, #>>, a subscript
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, region, country_code, segment)
SELECT
    o.payload ->> 'channel',
    (o.payload -> 'geo') ->> 'region',
    o.payload #>> '{geo,country}',
    o.payload['segment']::text
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S2 · a key taken from another column (D-22)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT o.payload ->> o.channel
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S3 · builders: jsonb_build_object, jsonb_build_array
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (full_name, segment)
SELECT
    jsonb_build_object('n', c.full_name, 'e', c.email)::text,
    jsonb_build_array(c.segment, c.status)::text
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S4 · modifiers: jsonb_set, ||, - key
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (region, status)
SELECT
    jsonb_set(c.attributes, '{tier}', to_jsonb(c.segment))::text,
    (c.attributes || jsonb_build_object('cc', c.country_code) - 'tmp')::text
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S5 · SQL/JSON: JSON_VALUE, JSON_QUERY over JSON_OBJECT, IS JSON OBJECT in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name, region)
SELECT
    JSON_VALUE(c.attributes, '$.name'),
    JSON_QUERY(JSON_OBJECT('id': c.id, 'cc': c.country_code), '$')::text
FROM staging.customers AS c
WHERE c.attributes IS JSON OBJECT;


-- -------------------------------------------------------------------------------------
-- S6 · predicates in WHERE: @>, ?, ?|, @? - all filters from the document
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (region)
SELECT o.channel
FROM staging.orders AS o
WHERE o.payload @> '{"vip": true}'
  AND o.payload ? 'promo'
  AND o.payload ?| ARRAY['a', 'b']
  AND o.payload @? '$.items[*] ? (@.qty > 1)';


-- -------------------------------------------------------------------------------------
-- S7a · jsonb_object_agg, grouped
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, share_of_region)
SELECT o.channel, length(jsonb_object_agg(o.id, o.amount)::text)
FROM staging.orders AS o
GROUP BY o.channel;


-- -------------------------------------------------------------------------------------
-- S7b · jsonb_agg with its own ORDER BY: the ordering column is influence (as string_agg)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.carrier_scorecard (carrier_id, carrier_name)
SELECT s.carrier_id, jsonb_agg(s.tracking ORDER BY s.shipped_at)::text
FROM staging.shipments AS s
GROUP BY s.carrier_id;


-- -------------------------------------------------------------------------------------
-- S8 · jsonb_array_elements in FROM with ordinality; a filter on an element
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, position)
SELECT o.customer_id, e.n
FROM staging.orders AS o,
     jsonb_array_elements(o.payload -> 'items') WITH ORDINALITY AS e(item, n)
WHERE e.item ->> 'sku' IS NOT NULL;


-- -------------------------------------------------------------------------------------
-- S9 · jsonb_each_text in FROM: key and value both come from the document
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (category_name)
SELECT kv.key || '=' || kv.value
FROM staging.products AS p, jsonb_each_text(p.dims) AS kv;


-- -------------------------------------------------------------------------------------
-- S10 · jsonb_object_keys in the select list
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT jsonb_object_keys(w.properties)
FROM staging.web_events AS w;


-- -------------------------------------------------------------------------------------
-- S11 · jsonb_to_recordset with a column-definition list
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (units, gross_amount)
SELECT r.qty, r.price
FROM staging.orders AS o,
     jsonb_to_recordset(o.payload -> 'items') AS r(sku text, qty int, price numeric);


-- -------------------------------------------------------------------------------------
-- S12 · jsonb_path_query (a set) and jsonb_path_query_first (a value) in the select list
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (orders, revenue)
SELECT
    jsonb_path_query(o.payload, '$.items[*].qty')::bigint,
    jsonb_path_query_first(o.payload, '$.total')::numeric
FROM staging.orders AS o;


-- -------------------------------------------------------------------------------------
-- S13 · JSON_TABLE with FOR ORDINALITY (D-30)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, units, revenue, growth_pct)
SELECT jt.pid, jt.qty, jt.price, jt.n
FROM staging.orders AS o,
     JSON_TABLE(
         o.payload, '$.items[*]'
         COLUMNS (
             n FOR ORDINALITY,
             pid bigint PATH '$.product_id',
             qty bigint PATH '$.qty',
             price numeric PATH '$.price'
         )
     ) AS jt;


-- -------------------------------------------------------------------------------------
-- S14 · JSON_TABLE with a PASSING argument: it is read too (D-30, D-22)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.sales_daily (region)
SELECT jt.sku
FROM staging.orders AS o,
     JSON_TABLE(
         o.payload, '$.items[*] ? (@.sku == $s)' PASSING o.discount_code AS s
         COLUMNS (sku text PATH '$.sku')
     ) AS jt;


-- -------------------------------------------------------------------------------------
-- S15 · XMLTABLE, filtered on the feed row
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (revenue)
SELECT x.amt
FROM staging.feed_xml AS f,
     XMLTABLE('/feed/row' PASSING f.doc COLUMNS cid bigint PATH '@id', amt numeric PATH 'amount') AS x
WHERE f.source = 'ERP';


-- -------------------------------------------------------------------------------------
-- S16 · xpath() picked by subscript
-- -------------------------------------------------------------------------------------
INSERT INTO mart."FeedSummary" ("Status Code", "CustomerID")
SELECT (xpath('/feed/@status', f.doc))[1]::text, f.id
FROM staging.feed_xml AS f;


-- -------------------------------------------------------------------------------------
-- S17 · xmlagg over xmlelement, ordered by another column
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (customers, level)
SELECT COUNT(s.id), xmlagg(xmlelement(name store, s.name) ORDER BY s.opened_on)::text
FROM core.dim_store AS s;


-- -------------------------------------------------------------------------------------
-- S18 · hstore: -> in the projection, ? and @> in WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, full_name)
SELECT d.device_id, d.attrs -> 'model'
FROM staging.device_attrs AS d
WHERE d.attrs ? 'model'
  AND d.attrs @> 'active=>1';


-- -------------------------------------------------------------------------------------
-- S19 · hstore each() in FROM
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (period_month, region)
SELECT d.updated_at::date, kv.key
FROM staging.device_attrs AS d, each(d.attrs) AS kv;
