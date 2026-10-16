-- =====================================================================================
-- B5 · T3 · INTERACTION — do document rules compose with B1-B4 rules?
--
-- Objective card: stress/pg/b5_semi_structured/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Documents meeting GROUP BY (D-2), windows (D-4), row selection (D-9), scalar
-- subqueries (D-12), DML (B3), ON CONFLICT (D-15) and set operations.
-- Every node is schema-qualified (D-25).
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · GROUP BY an extracted key: the document influences the sum
-- -------------------------------------------------------------------------------------
INSERT INTO mart.channel_mix (channel, revenue)
SELECT o.payload ->> 'channel', SUM(o.amount)
FROM staging.orders AS o
GROUP BY o.payload ->> 'channel';


-- -------------------------------------------------------------------------------------
-- S2 · a running sum over JSON_TABLE rows, ordered by its ordinality (D-4, D-26, D-30)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, running_revenue)
SELECT jt.pid, SUM(jt.price) OVER (PARTITION BY o.id ORDER BY jt.n)
FROM staging.orders AS o,
     JSON_TABLE(
         o.payload, '$.items[*]'
         COLUMNS (n FOR ORDINALITY, pid bigint PATH '$.product_id', price numeric PATH '$.price')
     ) AS jt;


-- -------------------------------------------------------------------------------------
-- S3 · ORDER BY an extracted key under LIMIT: the document decides which rows land (D-9)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (customer_id)
SELECT c.id
FROM staging.customers AS c
ORDER BY (c.attributes ->> 'score')::numeric DESC
LIMIT 10;


-- -------------------------------------------------------------------------------------
-- S4 · a jsonpath test inside a scalar subquery: a value of the count (D-12)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary (customer_id, lifetime_orders)
SELECT
    c.id,
    (SELECT COUNT(*)
     FROM staging.orders AS o
     WHERE o.customer_id = c.id
       AND o.payload @? '$.items[*] ? (@.qty > 5)')
FROM staging.customers AS c;


-- -------------------------------------------------------------------------------------
-- S5 · UPDATE ... SET payload = jsonb_set(payload, ...) FROM payments (D-11)
-- -------------------------------------------------------------------------------------
UPDATE staging.orders AS o
SET payload = jsonb_set(o.payload, '{paid}', to_jsonb(p.amount))
FROM staging.payments AS p
WHERE p.order_id = o.id
  AND p.status = 'OK';


-- -------------------------------------------------------------------------------------
-- S6 · an ON CONFLICT accumulator that merges documents (D-15)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.customer_summary AS cs (customer_id, status)
SELECT c.id, jsonb_build_object('seg', c.segment)::text
FROM staging.customers AS c
ON CONFLICT (customer_id) DO UPDATE
SET status = (cs.status::jsonb || EXCLUDED.status::jsonb)::text;


-- -------------------------------------------------------------------------------------
-- S7 · the same key taken from two different documents across UNION ALL
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level)
SELECT o.payload ->> 'src'
FROM staging.orders AS o
UNION ALL
SELECT w.properties ->> 'src'
FROM staging.web_events AS w;


-- -------------------------------------------------------------------------------------
-- S8 · DISTINCT ON an extracted key, with a tie-breaker (D-9)
-- -------------------------------------------------------------------------------------
INSERT INTO mart.top_customers (region, full_name)
SELECT DISTINCT ON (w.properties ->> 'campaign')
    w.properties ->> 'campaign',
    w.page
FROM staging.web_events AS w
ORDER BY w.properties ->> 'campaign', w.event_ts DESC;
