-- =====================================================================================
-- B8 · T4 · ADVERSARIAL — does procedural code fool the analyser?
--
-- Objective card: stress/pg/b8_procedural/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Every trap has a plausible wrong answer. Conventions: every earlier decision,
-- OBJECTIVES.md rules 1-9, D-39..D-42.
-- =====================================================================================


-- A1 — a function that is never called
CREATE FUNCTION mart.fn_never_called() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_a1 integer;
BEGIN
-- S1 · never called: its lineage stands (D-39)
    INSERT INTO mart.kpi_rollup (country_code) SELECT c.country_code FROM staging.customers AS c;
END;
$$;


-- A2 — a trigger function that is never bound
CREATE FUNCTION mart.trg_unbound() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_a2 integer;
BEGIN
-- S2 · never bound: NEW has no table, so no edge (D-41)
    INSERT INTO mart.events_log (kind) VALUES (NEW.status);
    RETURN NEW;
END;
$$;


-- A3 — one trigger function bound to two tables
CREATE FUNCTION mart.trg_stamp() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_a3 integer;
BEGIN
-- S3 · bound twice: each table's edges (D-41)
    INSERT INTO mart.events_log (id) VALUES (NEW.id);
    RETURN NEW;
END;
$$;

CREATE TRIGGER t_stamp_returns
AFTER INSERT ON staging.returns
FOR EACH ROW EXECUTE FUNCTION mart.trg_stamp();

CREATE TRIGGER t_stamp_shipments
AFTER INSERT ON staging.shipments
FOR EACH ROW EXECUTE FUNCTION mart.trg_stamp();


-- A4 — a function with its own search_path
CREATE FUNCTION mart.fn_pathed() RETURNS void
LANGUAGE plpgsql
SET search_path = core
AS $$
DECLARE
    v_a4 integer;
BEGIN
-- S4 · `ledger` binds through the function's path: core.ledger (D-23)
    INSERT INTO ledger (account) SELECT l.account FROM staging.ledger AS l;
END;
$$;


-- A5 — SQL text inside a RAISE
CREATE FUNCTION mart.fn_noisy() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_a5 integer;
BEGIN
    RAISE NOTICE 'INSERT INTO mart.kpi_rollup (segment) SELECT c.email FROM staging.customers c; done';
-- S5 · the real statement after it
    INSERT INTO mart.kpi_rollup (segment) SELECT c.segment FROM staging.customers AS c WHERE c.status = 'A';
END;
$$;


-- A6 — EXECUTE of a parameter
CREATE PROCEDURE mart.pr_run(p_sql text)
LANGUAGE plpgsql AS $$
DECLARE
    v_a6 integer;
BEGIN
-- S6 · the whole statement is a parameter: declared (D-42)
    EXECUTE p_sql;
END;
$$;


-- A7 — WHEN OTHERS THEN NULL
CREATE FUNCTION mart.fn_swallow() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_a7 integer;
BEGIN
-- S7 · a write whose failure is swallowed
    INSERT INTO mart.top_customers (region) SELECT s.region FROM core.dim_store AS s;
EXCEPTION WHEN OTHERS THEN
    NULL;
END;
$$;


-- A8 — PERFORM
CREATE FUNCTION mart.fn_perform() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_a8 integer;
BEGIN
-- S8 · PERFORM discards its rows: no edge
    PERFORM o.id, o.amount FROM staging.orders AS o WHERE o.status = 'PAID';
END;
$$;


-- A9 — a RETURN QUERY function dropped and re-created before its read
CREATE FUNCTION mart.fn_swap() RETURNS TABLE (x text)
LANGUAGE plpgsql AS $$
DECLARE
    v_a9 integer;
BEGIN
    RETURN QUERY SELECT c.email FROM staging.customers AS c;
END;
$$;

DROP FUNCTION mart.fn_swap();

CREATE FUNCTION mart.fn_swap() RETURNS TABLE (x text)
LANGUAGE plpgsql AS $$
DECLARE
    v_a9b integer;
BEGIN
    RETURN QUERY SELECT c.full_name FROM staging.customers AS c;
END;
$$;

-- S9 · the read sees the second body (D-40, D-31)
INSERT INTO mart.top_customers (full_name) SELECT f.x FROM mart.fn_swap() AS f;


-- A10 — a variable named like a column
CREATE FUNCTION mart.fn_shadow() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    amount numeric;
BEGIN
-- S10 · `amount` is the variable; p.amount is the column
    SELECT MAX(p.amount) INTO amount FROM staging.payments AS p;
-- S11 · with no table in scope, `amount` in VALUES is the variable
    INSERT INTO mart.top_customers (revenue) VALUES (amount);
END;
$$;


-- A11 — a $body$-quoted body holding $$ in a literal
CREATE FUNCTION mart.fn_tagged() RETURNS void
LANGUAGE plpgsql AS $body$
DECLARE
    v_msg text := 'costs $$5; INSERT INTO mart.kpi_rollup (level) SELECT 1';
BEGIN
-- S12 · the real statement
    INSERT INTO mart.kpi_rollup (level) SELECT o.currency FROM staging.orders AS o;
END;
$body$;
