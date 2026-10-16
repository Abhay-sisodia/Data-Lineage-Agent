-- =====================================================================================
-- B5 · T2 · DEPTH — does document lineage survive nesting?
--
-- Objective card: stress/pg/b5_semi_structured/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Documents built at one level and taken apart at another. Lineage is column-level
-- (D-29): a document built from two columns carries both into anything extracted from it.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · a document built from two columns, extended, then a key extracted and grouped
--   Column-level: the extracted 'seg' reads the whole document, so both columns.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, revenue)
WITH docs AS (
    SELECT c.id, jsonb_build_object('seg', c.segment, 'cc', c.country_code) AS d
    FROM staging.customers AS c
),
l2 AS (
    SELECT docs.id, docs.d || jsonb_build_object('n', 1) AS d
    FROM docs
),
l3 AS (
    SELECT l2.d ->> 'seg' AS seg, l2.id
    FROM l2
)
SELECT l3.seg, COUNT(l3.id)
FROM l3
GROUP BY l3.seg;


-- -------------------------------------------------------------------------------------
-- S2 · JSON_TABLE in a CTE, summed above
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, units)
WITH items AS (
    SELECT jt.pid, jt.qty
    FROM staging.orders AS o,
         JSON_TABLE(
             o.payload, '$.items[*]'
             COLUMNS (pid bigint PATH '$.product_id', qty int PATH '$.qty')
         ) AS jt
    WHERE o.status = 'PAID'
)
SELECT i.pid, SUM(i.qty)
FROM items AS i
GROUP BY i.pid;


-- -------------------------------------------------------------------------------------
-- S3 · elements of elements: an array inside each array element
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel)
SELECT t.tag
FROM staging.orders AS o,
     jsonb_array_elements(o.payload -> 'items') AS i(item),
     jsonb_array_elements_text(i.item -> 'tags') AS t(tag);


-- -------------------------------------------------------------------------------------
-- S4 · xml built in a CTE with xmlelement / xmlattributes, then queried with xpath
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (segment)
WITH x AS (
    SELECT xmlelement(name c, xmlattributes(c.segment AS seg), c.full_name) AS doc
    FROM staging.customers AS c
)
SELECT (xpath('/c/@seg', x.doc))[1]::text
FROM x;


-- -------------------------------------------------------------------------------------
-- S5 · hstore converted to jsonb in a derived table, then a key extracted
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name)
SELECT j.doc ->> 'model'
FROM (
    SELECT hstore_to_jsonb(d.attrs) AS doc
    FROM staging.device_attrs AS d
) AS j;


-- -------------------------------------------------------------------------------------
-- S6 · an ordinal carried up a derived table and filtered there (D-26)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id, position)
SELECT z.cid, z.n
FROM (
    SELECT o.customer_id AS cid, e.n
    FROM staging.orders AS o,
         jsonb_array_elements(o.payload -> 'items') WITH ORDINALITY AS e(item, n)
) AS z
WHERE z.n = 1;


-- -------------------------------------------------------------------------------------
-- S7 · an array built by jsonb_agg, then exploded again a level up
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT e ->> 'ch'
FROM (
    SELECT jsonb_agg(jsonb_build_object('ch', o.channel)) AS arr
    FROM staging.orders AS o
) AS a,
     jsonb_array_elements(a.arr) AS e;
