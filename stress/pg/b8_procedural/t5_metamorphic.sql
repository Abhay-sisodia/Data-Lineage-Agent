-- =====================================================================================
-- B8 · T5 · METAMORPHIC — do equivalent procedural spellings give identical lineage?
--
-- Objective card: stress/pg/b8_procedural/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P6 MUST BE EQUAL in
-- their facts. Their bands may differ (rule 5): the pair check compares facts, not bands.
--
-- Then one realistic nightly load: a parameter, a SELECT INTO, a set-based load, a loop,
-- an undecidable partition EXECUTE, a run log, and a re-raising handler.
-- =====================================================================================


-- P1 · a static INSERT in a function vs at top level
CREATE FUNCTION mart.fn_p1() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_p1 integer;
BEGIN
-- @pair P1 A
    INSERT INTO mart.channel_mix (channel)
    SELECT o.channel FROM staging.orders AS o WHERE o.status = 'PAID';
END;
$$;

-- @pair P1 B
INSERT INTO mart.channel_mix (channel)
SELECT o.channel FROM staging.orders AS o WHERE o.status = 'PAID';


-- P2 · a FOR loop vs INSERT ... SELECT
CREATE FUNCTION mart.fn_p2() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    r2 record;
BEGIN
    FOR r2 IN SELECT c.id, c.segment FROM staging.customers AS c WHERE c.status = 'A'
    LOOP
-- @pair P2 A
        INSERT INTO mart.top_customers (customer_id, region) VALUES (r2.id, r2.segment);
    END LOOP;
END;
$$;

-- @pair P2 B
INSERT INTO mart.top_customers (customer_id, region)
SELECT c.id, c.segment FROM staging.customers AS c WHERE c.status = 'A';


-- P3 · constant dynamic SQL vs static
DO $$
DECLARE
    v_p3 integer;
BEGIN
-- @pair P3 A
    EXECUTE 'INSERT INTO mart.kpi_rollup (segment) SELECT c.segment FROM staging.customers AS c WHERE c.status = ''B''';
END;
$$;

-- @pair P3 B
INSERT INTO mart.kpi_rollup (segment)
SELECT c.segment FROM staging.customers AS c WHERE c.status = 'B';


-- P4 · a RETURN QUERY function read vs the same query as a derived table
CREATE FUNCTION mart.fn_p4(p_cur text)
RETURNS TABLE (oid bigint, amt numeric)
LANGUAGE plpgsql AS $$
DECLARE
    v_p4 integer;
BEGIN
    RETURN QUERY SELECT o.id, o.amount FROM staging.orders AS o WHERE o.currency = p_cur;
END;
$$;

-- @pair P4 A
INSERT INTO mart.sales_daily (orders, net_amount)
SELECT f.oid, f.amt FROM mart.fn_p4('USD') AS f;

-- @pair P4 B
INSERT INTO mart.sales_daily (orders, net_amount)
SELECT f.oid, f.amt
FROM (SELECT o.id AS oid, o.amount AS amt FROM staging.orders AS o WHERE o.currency = 'USD') AS f;


-- P5 · a DO block vs the plain statement
DO $$
DECLARE
    v_p5 integer;
BEGIN
-- @pair P5 A
    INSERT INTO core.dim_carrier (name, mode)
    SELECT p.method, p.currency FROM staging.payments AS p WHERE p.status = 'OK';
END;
$$;

-- @pair P5 B
INSERT INTO core.dim_carrier (name, mode)
SELECT p.method, p.currency FROM staging.payments AS p WHERE p.status = 'OK';


-- P6 · format('%I.%I') vs concatenation, both constant
DO $$
DECLARE
    v_p6 integer;
BEGIN
-- @pair P6 A
    EXECUTE format('INSERT INTO %I.%I (region) SELECT s.region FROM core.dim_store AS s', 'mart', 'channel_mix');
-- @pair P6 B
    EXECUTE 'INSERT INTO ' || 'mart.channel_mix' || ' (region) SELECT s.region FROM core.dim_store AS s';
END;
$$;


-- =====================================================================================
-- The realistic procedure: a nightly load
-- =====================================================================================
CREATE PROCEDURE mart.pr_nightly_load(p_day date)
LANGUAGE plpgsql AS $$
DECLARE
    v_rows bigint;
    v_fx   numeric;
    v_part text;
    rc     record;
BEGIN
-- R1 · the day's EUR rate, into a variable
    SELECT fx.rate_to_usd INTO v_fx
    FROM staging.fx_rates AS fx
    WHERE fx.currency = 'EUR' AND fx.rate_date = p_day;
-- R2 · the set-based load, converted by the variable
    INSERT INTO core.fct_payment (payment_id, order_id, method, amount, amount_usd, paid_date, status)
    SELECT p.id, p.order_id, p.method, p.amount, p.amount * v_fx, p.paid_at::date, p.status
    FROM staging.payments AS p
    WHERE p.paid_at::date = p_day AND p.currency = 'EUR';
    GET DIAGNOSTICS v_rows = ROW_COUNT;
    FOR rc IN SELECT c.id, c.full_name, c.segment
              FROM staging.customers AS c
              JOIN staging.orders AS o ON o.customer_id = c.id
              WHERE o.order_ts::date = p_day
    LOOP
-- R3 · each customer touched today
        UPDATE mart.customer_summary AS cs
        SET segment = rc.segment, last_order_date = p_day
        WHERE cs.customer_id = rc.id;
    END LOOP;
-- R4 · the partition's name, from the parameter
    v_part := 'web_events_' || to_char(p_day, 'YYYYMMDD');
-- R5 · so this EXECUTE is undecidable
    EXECUTE format('INSERT INTO staging.%I SELECT * FROM staging.web_events WHERE event_ts::date = $1', v_part) USING p_day;
-- R6 · the run log
    INSERT INTO mart.events_log (id, kind, rolled_up)
    VALUES (v_rows, 'nightly', true);
EXCEPTION WHEN OTHERS THEN
    RAISE;
END;
$$;

CALL mart.pr_nightly_load(DATE '2026-10-03');
