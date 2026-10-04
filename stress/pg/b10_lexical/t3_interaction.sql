-- =====================================================================================
-- B10 · T3 · INTERACTION — do the lexical forms compose in one statement?
--
-- Objective card: stress/pg/b10_lexical/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql, plus pg_trgm. PostgreSQL: 17. Run with psql.
--
-- Conventions: every earlier decision, plus D-35..D-38. A prepared statement's lineage
-- is keyed at its EXECUTE (D-35).
-- =====================================================================================


\set cc 'DE'
-- -------------------------------------------------------------------------------------
-- S1 · PREPARE: a parameter, a custom operator under CASE, and dollar literals
-- -------------------------------------------------------------------------------------
PREPARE p_mix(text) AS
INSERT INTO mart.customer_summary (customer_id, avg_basket, full_name)
SELECT c.id,
       CASE WHEN c.status = $$A$$ THEN c.full_name <-> c.email ELSE 0 END,
       $seg$[$seg$ || c.segment || ']'
FROM staging.customers AS c
WHERE c.country_code = $1;


-- -------------------------------------------------------------------------------------
-- S2 · EXECUTE with a psql variable as its argument
-- -------------------------------------------------------------------------------------
EXECUTE p_mix(:'cc');


-- -------------------------------------------------------------------------------------
-- S3 · PREPARE an UPDATE ... FROM with an E'' literal
-- -------------------------------------------------------------------------------------
PREPARE p_upd(text) AS
UPDATE mart.customer_summary AS cs
SET segment = c.segment || E'\t'
FROM staging.customers AS c
WHERE c.id = cs.customer_id
  AND c.status = $1;


-- -------------------------------------------------------------------------------------
-- S4 · EXECUTE the UPDATE
-- -------------------------------------------------------------------------------------
EXECUTE p_upd('A');


-- -------------------------------------------------------------------------------------
-- S5 · PREPARE a MERGE whose arm condition compares an aggregate to $1
-- -------------------------------------------------------------------------------------
PREPARE p_merge(numeric) AS
MERGE INTO mart.top_customers AS t
USING (
    SELECT p.order_id, SUM(p.amount) AS amt
    FROM staging.payments AS p
    GROUP BY p.order_id
) AS s
ON t.customer_id = s.order_id
WHEN MATCHED AND s.amt > $1 THEN
    UPDATE SET revenue = s.amt
WHEN NOT MATCHED THEN
    INSERT (customer_id, revenue) VALUES (s.order_id, s.amt);


-- -------------------------------------------------------------------------------------
-- S6 · EXECUTE the MERGE
-- -------------------------------------------------------------------------------------
EXECUTE p_merge(100);


-- -------------------------------------------------------------------------------------
-- S7 · PREPARE a writable CTE whose RETURNING feeds a second write
-- -------------------------------------------------------------------------------------
PREPARE p_cte(text) AS
WITH moved AS (
    INSERT INTO mart.events_log (id, kind)
    SELECT e.id, e.kind
    FROM staging.events_log AS e
    WHERE e.kind = $1
    RETURNING id, kind
)
INSERT INTO mart.kpi_rollup (orders, level)
SELECT m.id, m.kind
FROM moved AS m;


-- -------------------------------------------------------------------------------------
-- S8 · EXECUTE the writable CTE
-- -------------------------------------------------------------------------------------
EXECUTE p_cte('click');


-- -------------------------------------------------------------------------------------
-- S9 · E'', U&'' and OPERATOR(pg_catalog.…) together in one WHERE
-- -------------------------------------------------------------------------------------
INSERT INTO core.fct_payment (method, amount)
SELECT p.method, p.amount
FROM staging.payments AS p
WHERE p.method OPERATOR(pg_catalog.<>) E'cash\'s'
  AND p.currency = U&'\0045UR'
  AND p.amount OPERATOR(pg_catalog.>) 0;


-- -------------------------------------------------------------------------------------
-- S10 · PREPARE, DEALLOCATE, PREPARE again under the same name with a new body
-- -------------------------------------------------------------------------------------
PREPARE p_r AS
INSERT INTO mart.channel_mix (orders)
SELECT count(o.customer_id) FROM staging.orders AS o;

DEALLOCATE p_r;

PREPARE p_r AS
INSERT INTO mart.channel_mix (orders)
SELECT count(o.id) FROM staging.orders AS o WHERE o.channel = 'web';


-- -------------------------------------------------------------------------------------
-- S11 · EXECUTE without parentheses: the second body only
-- -------------------------------------------------------------------------------------
EXECUTE p_r;


\set reg2 'NORTH'
-- -------------------------------------------------------------------------------------
-- S12 · PREPARE a SELECT with a comment, a psql variable, $1 and a dollar literal
-- -------------------------------------------------------------------------------------
PREPARE p_v(int) AS
SELECT d.name /* the name; not d.status */, d.capacity OPERATOR(pg_catalog.+) $1 AS cap
FROM core.dim_warehouse AS d
WHERE d.region = :'reg2'
  AND d.status <> $$closed$$;


-- -------------------------------------------------------------------------------------
-- S13 · EXECUTE it: into <result>
-- -------------------------------------------------------------------------------------
EXECUTE p_v(10);
