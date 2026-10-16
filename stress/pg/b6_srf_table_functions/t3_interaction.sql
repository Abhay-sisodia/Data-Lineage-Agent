-- =====================================================================================
-- B6 · T3 · INTERACTION — do set-returning functions compose with B1-B4 rules?
--
-- Objective card: stress/pg/b6_srf_table_functions/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- SRFs meeting GROUP BY (D-2), windows (D-4), row selection (D-9), outer joins, DML
-- (B3) and scalar subqueries (D-12). Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · GROUP BY an element: the array influences the aggregate (D-2)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT t.tag, SUM(o.amount)
FROM staging.orders AS o, unnest(o.tags) AS t(tag)
GROUP BY t.tag;


-- -------------------------------------------------------------------------------------
-- S2 · a window ordered by the ordinal and partitioned by the product (D-4, D-26)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (full_name, position, revenue)
SELECT
    t.tag,
    t.n,
    SUM(p.list_price) OVER (PARTITION BY p.id ORDER BY t.n)
FROM staging.products AS p, unnest(p.tags) WITH ORDINALITY AS t(tag, n);


-- -------------------------------------------------------------------------------------
-- S3 · ORDER BY an element under LIMIT: the element decides which rows land (D-9)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT t.tag
FROM staging.products AS p, unnest(p.tags) AS t(tag)
ORDER BY t.tag
LIMIT 10;


-- -------------------------------------------------------------------------------------
-- S4 · LEFT JOIN LATERAL unnest ON true, then an OUTER WHERE on the element
--   The LEFT keeps customers whose email splits to nothing; the outer WHERE then removes
--   rows, so it is a filter from the array's source.
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, segment)
SELECT c.id, t.part
FROM staging.customers AS c
LEFT JOIN LATERAL unnest(string_to_array(c.email, '@')) AS t(part) ON true
WHERE t.part IS DISTINCT FROM 'example.com';


-- -------------------------------------------------------------------------------------
-- S5 · UPDATE ... FROM an unnest with ordinality: the first tag becomes the category
--   p.id = d.product_id joins the target (D-11); t.n = 1 is a filter from the array.
-- -------------------------------------------------------------------------------------
UPDATE core.dim_product AS d
SET category_name = t.tag
FROM staging.products AS p, unnest(p.tags) WITH ORDINALITY AS t(tag, n)
WHERE p.id = d.product_id
  AND t.n = 1;


-- -------------------------------------------------------------------------------------
-- S6 · a series bounded by a column, summed per grouping key
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (region, revenue)
SELECT w.region, SUM(g)
FROM core.dim_warehouse AS w, generate_series(1, w.capacity) AS g
GROUP BY w.region;


-- -------------------------------------------------------------------------------------
-- S7 · an SRF inside a scalar subquery: its WHERE is a value of the column (D-12)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (segment, customers)
SELECT
    c.full_name,
    (SELECT COUNT(*) FROM unnest(string_to_array(c.email, '.')) AS x(p) WHERE x.p <> '')
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S8 · DISTINCT ON an element, ordered by a tie-breaker (D-9)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, customer_id)
SELECT DISTINCT ON (t.tag) t.tag, o.customer_id
FROM staging.orders AS o, unnest(o.tags) AS t(tag)
ORDER BY t.tag, o.order_ts DESC;
