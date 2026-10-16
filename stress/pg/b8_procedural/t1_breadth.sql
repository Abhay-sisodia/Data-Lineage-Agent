-- =====================================================================================
-- B8 · T1 · BREADTH — is each procedural form supported at all?
--
-- Objective card: stress/pg/b8_procedural/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17 (procedures 11+, CALL 11+).
--
-- Conventions: every earlier decision, the Oracle procedural conventions (OBJECTIVES.md
-- rules 1-9), plus D-39 (lineage at the definition), D-40 (RETURN QUERY inlined),
-- D-41 (triggers once, at their binding, band 2) and D-42 (dynamic SQL when decidable).
-- Routines are schema-qualified. Each opens with a DECLARE so triage can place its
-- first statement (OBJECTIVES.md, harness note).
-- =====================================================================================


-- R1 — a function whose body is one static INSERT
CREATE FUNCTION mart.fn_load_channel() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_r1 integer;
BEGIN
-- S1 · the static INSERT: band 0, at its own line (D-39)
    INSERT INTO mart.channel_mix (channel, revenue)
    SELECT o.channel, SUM(o.amount)
    FROM staging.orders AS o
    WHERE o.status = 'PAID'
    GROUP BY o.channel;
END;
$$;

-- S2 · calling it: counted, no edges (D-39)
SELECT mart.fn_load_channel();


-- R3 — a procedure with a parameter
CREATE PROCEDURE mart.pr_region(p_region text)
LANGUAGE plpgsql AS $$
DECLARE
    v_r3 integer;
BEGIN
-- S3 · a parameter in WHERE: a band-1 filter from the variable
    INSERT INTO mart.kpi_rollup (region, revenue)
    SELECT s.region, SUM(o.amount)
    FROM staging.orders AS o
    JOIN core.dim_store AS s ON s.id = o.store_id
    WHERE s.region = p_region
    GROUP BY s.region;
END;
$$;

-- S4 · CALL: counted, no edges
CALL mart.pr_region('EMEA');


-- R5 — SELECT INTO a variable, an assignment, the variables as values
CREATE FUNCTION mart.fn_pay_total() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_total numeric;
    v_rate  numeric;
BEGIN
-- S5 · SELECT ... INTO a variable
    SELECT SUM(p.amount) INTO v_total
    FROM staging.payments AS p
    WHERE p.status = 'OK';
-- S6 · an assignment
    v_rate := v_total * 1.1;
-- S7 · the variables as values
    INSERT INTO mart.kpi_rollup (level, revenue, orders)
    VALUES ('payments', v_rate, v_total);
END;
$$;


-- R8 — a FOR loop over a query: the record is transparent
CREATE FUNCTION mart.fn_loop_cust() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT c.id, c.full_name, c.segment
             FROM staging.customers AS c
             WHERE c.status = 'A'
    LOOP
-- S8 · the loop body: r.f traced to the query (band 1)
        UPDATE mart.customer_summary AS cs
        SET full_name = r.full_name
        WHERE cs.customer_id = r.id;
    END LOOP;
END;
$$;


-- R9 — an explicit cursor FETCHed into variables
CREATE FUNCTION mart.fn_cursor_store() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    c_store CURSOR FOR SELECT s.id, s.name FROM core.dim_store AS s WHERE s.status = 'OPEN';
    v_store_id   integer;
    v_store_name text;
BEGIN
    OPEN c_store;
-- S9 · FETCH into variables: bound by position
    FETCH c_store INTO v_store_id, v_store_name;
-- S10 · the fetched variables as values
    INSERT INTO mart.sales_daily (store_id, region)
    VALUES (v_store_id, v_store_name);
    CLOSE c_store;
END;
$$;


-- R11 — a RETURN QUERY function: nothing at the definition (D-40)
CREATE FUNCTION mart.fn_paid(p_ch text)
RETURNS TABLE (ch text, amt numeric)
LANGUAGE plpgsql AS $$
DECLARE
    v_r11 integer;
BEGIN
    RETURN QUERY
        SELECT o.channel, o.amount
        FROM staging.orders AS o
        WHERE o.status = 'PAID' AND o.channel = p_ch;
END;
$$;

-- S11 · reading it in FROM: inlined like a view (D-40)
INSERT INTO mart.top_customers (full_name, revenue)
SELECT f.ch, f.amt
FROM mart.fn_paid('web') AS f;


-- R12 — a scalar function
CREATE FUNCTION mart.fn_cust_value(p_cust bigint) RETURNS numeric
LANGUAGE plpgsql AS $$
DECLARE
    v_value numeric;
BEGIN
-- S12 · SELECT INTO inside the scalar function
    SELECT SUM(o.amount) INTO v_value
    FROM staging.orders AS o
    WHERE o.customer_id = p_cust;
    RETURN v_value;
END;
$$;

-- S13 · the scalar function in a query: its return value's columns, not its argument
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT c.id, mart.fn_cust_value(c.id)
FROM staging.customers AS c;


-- R14 — a DO block
DO $$
DECLARE
    v_r14 integer;
BEGIN
-- S14 · a DO block runs where it stands (D-39)
    INSERT INTO mart.events_log (id, kind)
    SELECT e.id, e.kind
    FROM staging.events_log AS e
    WHERE e.payload_size > 0;
END;
$$;


-- R15 — EXECUTE of constant text
DO $$
DECLARE
    v_r15 integer;
BEGIN
-- S15 · constant dynamic SQL: analysed, band 2 (D-42)
    EXECUTE 'INSERT INTO mart.kpi_rollup (segment) SELECT c.segment FROM staging.customers AS c WHERE c.country_code = ''DE''';
END;
$$;


-- R16 — EXECUTE format() of constants
DO $$
DECLARE
    v_schema text := 'mart';
BEGIN
-- S16 · format() of a variable assigned only a constant: decidable (D-42)
    EXECUTE format('INSERT INTO %I.%I (country_code) SELECT c.country_code FROM staging.customers AS c', v_schema, 'kpi_rollup');
END;
$$;


-- R17 — EXECUTE of text built from a parameter
CREATE PROCEDURE mart.pr_dyn(p_table text)
LANGUAGE plpgsql AS $$
DECLARE
    v_r17 integer;
BEGIN
-- S17 · undecidable: declared dynamic_sql, never guessed (D-42)
    EXECUTE format('INSERT INTO mart.%I (level) SELECT o.channel FROM staging.orders AS o', p_table);
END;
$$;


-- R18 — a trigger on INSERT: NEW
CREATE FUNCTION mart.trg_order_audit() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_r18 integer;
BEGIN
-- S18 · NEW.x is the triggering table's column: band 2, once (D-41)
    INSERT INTO mart.events_log (id, kind)
    VALUES (NEW.id, NEW.status);
    RETURN NEW;
END;
$$;

CREATE TRIGGER t_order_audit
AFTER INSERT ON staging.orders
FOR EACH ROW EXECUTE FUNCTION mart.trg_order_audit();


-- R19 — a trigger on UPDATE: OLD
CREATE FUNCTION mart.trg_pay_history() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_r19 integer;
BEGIN
-- S19 · OLD.x on an UPDATE trigger
    INSERT INTO mart.top_customers (customer_id, revenue)
    VALUES (OLD.order_id, OLD.amount);
    RETURN NEW;
END;
$$;

CREATE TRIGGER t_pay_history
BEFORE UPDATE ON staging.payments
FOR EACH ROW EXECUTE FUNCTION mart.trg_pay_history();


-- R20 — a procedure that commits
CREATE PROCEDURE mart.pr_close_stores()
LANGUAGE plpgsql AS $$
DECLARE
    v_r20 integer;
BEGIN
-- S20 · an UPDATE, then COMMIT
    UPDATE core.dim_store AS s
    SET status = 'CLOSED'
    WHERE s.opened_on < DATE '2000-01-01';
    COMMIT;
END;
$$;

-- S21 · CALL: counted, no edges
CALL mart.pr_close_stores();


-- R22 — a RETURN NEXT function
CREATE FUNCTION mart.fn_gen_regions() RETURNS SETOF text
LANGUAGE plpgsql AS $$
DECLARE
    rr record;
BEGIN
    FOR rr IN SELECT s.region FROM core.dim_store AS s
    LOOP
        RETURN NEXT rr.region;
    END LOOP;
END;
$$;

-- S22 · reading a RETURN NEXT function: declared source_unavailable (D-40)
INSERT INTO mart.channel_mix (region)
SELECT g.r
FROM mart.fn_gen_regions() AS g(r);


-- R23 — an exception block
CREATE FUNCTION mart.fn_upsert_ledger() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_r23 integer;
BEGIN
-- S23 · the protected statement
    INSERT INTO core.ledger (id, account)
    SELECT l.id, l.account FROM staging.ledger AS l;
EXCEPTION WHEN unique_violation THEN
-- S24 · the handler's statement is analysed too
    UPDATE core.ledger AS cl
    SET balance = cl.balance + 1
    WHERE cl.status = 'OPEN';
END;
$$;


-- R25 — GET DIAGNOSTICS
CREATE FUNCTION mart.fn_count_rows() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_rows bigint;
BEGIN
-- S25 · a write; then GET DIAGNOSTICS, which reads no column
    INSERT INTO mart.events_log (rolled_up)
    SELECT true FROM staging.events_log AS e WHERE e.kind = 'x';
    GET DIAGNOSTICS v_rows = ROW_COUNT;
-- S26 · the count as a value
    INSERT INTO mart.kpi_rollup (customers) VALUES (v_rows);
END;
$$;
