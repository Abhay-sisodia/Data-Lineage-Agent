-- =====================================================================================
-- B8 · T2 · DEPTH — does procedural support survive nesting?
--
-- Objective card: stress/pg/b8_procedural/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Nested blocks, chained variables, loops in loops, functions calling functions, and
-- a RETURN QUERY function over a view over a CTE chain. Conventions: every earlier
-- decision, OBJECTIVES.md rules 1-9, D-39..D-42.
-- =====================================================================================


-- R1 — blocks nested three deep, one value carried through four assignments
CREATE FUNCTION mart.fn_nested() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_a numeric;
BEGIN
-- S1 · depth 1: SELECT INTO
    SELECT MAX(o.amount) INTO v_a FROM staging.orders AS o WHERE o.currency = 'EUR';
    DECLARE
        v_b numeric;
    BEGIN
-- S2 · depth 2
        v_b := v_a + 1;
        DECLARE
            v_c numeric;
            v_d numeric;
        BEGIN
-- S3 · depth 3
            v_c := v_b * 2;
-- S4 · depth 3
            v_d := round(v_c);
-- S5 · depth 3: the last variable as a value
            INSERT INTO mart.kpi_rollup (revenue) VALUES (v_d);
        END;
    END;
END;
$$;


-- R2 — a loop inside a loop
CREATE FUNCTION mart.fn_loop2() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    r_store record;
    r_ord   record;
BEGIN
    FOR r_store IN SELECT s.id, s.region FROM core.dim_store AS s WHERE s.status = 'OPEN'
    LOOP
        FOR r_ord IN SELECT o.amount, o.channel FROM staging.orders AS o
                     WHERE o.store_id = r_store.id
        LOOP
-- S6 · two records, from two loops
            INSERT INTO mart.sales_daily (region, gross_amount)
            VALUES (r_store.region, r_ord.amount);
        END LOOP;
    END LOOP;
END;
$$;


-- R3 — a scalar function calling a scalar function
CREATE FUNCTION mart.fn_unit_cost(p_prod bigint) RETURNS numeric
LANGUAGE plpgsql AS $$
DECLARE
    v_cost numeric;
BEGIN
-- S7 · the inner function
    SELECT pr.cost INTO v_cost FROM staging.products AS pr WHERE pr.id = p_prod;
    RETURN v_cost;
END;
$$;

CREATE FUNCTION mart.fn_line_margin(p_line bigint) RETURNS numeric
LANGUAGE plpgsql AS $$
DECLARE
    v_margin numeric;
BEGIN
-- S8 · the outer function: a column, and the inner function's return value
    SELECT ol.unit_price - mart.fn_unit_cost(ol.product_id) INTO v_margin
    FROM staging.order_lines AS ol
    WHERE ol.id = p_line;
    RETURN v_margin;
END;
$$;

-- S9 · the outer function in a grouped query
INSERT INTO mart.product_performance (product_id, margin)
SELECT ol.product_id, SUM(mart.fn_line_margin(ol.id))
FROM staging.order_lines AS ol
GROUP BY ol.product_id;


-- R4 — a RETURN QUERY function over a view over a CTE chain
-- S10 · the view
CREATE VIEW mart.v_paid_orders AS
WITH a AS (SELECT o.id, o.customer_id, o.amount, o.status FROM staging.orders AS o),
     b AS (SELECT a.customer_id, a.amount FROM a WHERE a.status = 'PAID')
SELECT b.customer_id, b.amount FROM b;

CREATE FUNCTION mart.fn_cust_paid(p_min numeric)
RETURNS TABLE (cid bigint, total numeric)
LANGUAGE plpgsql AS $$
DECLARE
    v_r4 integer;
BEGIN
    RETURN QUERY
        SELECT v.customer_id, SUM(v.amount)
        FROM mart.v_paid_orders AS v
        GROUP BY v.customer_id
        HAVING SUM(v.amount) > p_min;
END;
$$;

-- S11 · reading it: function, view, CTE chain, all inlined
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT f.cid, f.total
FROM mart.fn_cust_paid(100) AS f;


-- R5 — constant dynamic SQL inside a loop
DO $$
DECLARE
    v_i integer;
BEGIN
    FOR v_i IN 1..3
    LOOP
-- S12 · constant text: decidable however often it runs (band 2)
        EXECUTE 'INSERT INTO core.dim_carrier (name) SELECT sh.status FROM staging.shipments AS sh';
    END LOOP;
END;
$$;


-- R6 — dynamic SQL built from the loop variable
DO $$
DECLARE
    v_tab text;
BEGIN
    FOREACH v_tab IN ARRAY ARRAY['kpi_rollup', 'channel_mix']
    LOOP
-- S13 · a different statement on every pass: undecidable (D-42)
        EXECUTE format('INSERT INTO mart.%I (region) SELECT s.region FROM core.dim_store AS s', v_tab);
    END LOOP;
END;
$$;


-- R7 — a procedure calling a procedure
CREATE PROCEDURE mart.pr_inner()
LANGUAGE plpgsql AS $$
DECLARE
    v_r7 integer;
BEGIN
-- S14 · the inner procedure's write, at its definition (D-39)
    INSERT INTO core.ledger (status) SELECT l.currency FROM staging.ledger AS l;
END;
$$;

CREATE PROCEDURE mart.pr_outer()
LANGUAGE plpgsql AS $$
DECLARE
    v_r7b integer;
BEGIN
    CALL mart.pr_inner();
END;
$$;

CALL mart.pr_outer();
