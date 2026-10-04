-- =====================================================================================
-- B11 · T5 · TWICE — one pipeline written two ways must give identical lineage
--
-- Objective card: stress/pg/b11_integration/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Pairs tagged `-- @pair Pn A|B`, checked by stress/pg/triage.py. P1-P6 MUST BE EQUAL in
-- their facts (bands may differ, B8 rule 5). Then one realistic incremental load: a
-- watermark variable, a converted load, a run log, and its CALL.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S0 · the view P1 A reads
-- -------------------------------------------------------------------------------------
CREATE VIEW mart.v_p1 AS
SELECT o.customer_id, SUM(o.amount) AS amt
FROM staging.orders AS o
WHERE o.status = 'PAID'
GROUP BY o.customer_id;

-- P1 · a view vs the same query as a CTE
-- @pair P1 A
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
SELECT v.customer_id, v.amt FROM mart.v_p1 AS v;

-- @pair P1 B
INSERT INTO mart.customer_summary (customer_id, lifetime_value)
WITH v AS (
    SELECT o.customer_id, SUM(o.amount) AS amt
    FROM staging.orders AS o
    WHERE o.status = 'PAID'
    GROUP BY o.customer_id
)
SELECT v.customer_id, v.amt FROM v;


-- P2 · set-based vs a FOR loop
-- @pair P2 A
INSERT INTO mart.top_customers (customer_id, full_name)
SELECT c.id, c.full_name FROM staging.customers AS c WHERE c.segment = 'VIP';

CREATE FUNCTION mart.fn_p2() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
    FOR r IN SELECT c.id, c.full_name FROM staging.customers AS c WHERE c.segment = 'VIP'
    LOOP
-- @pair P2 B
        INSERT INTO mart.top_customers (customer_id, full_name) VALUES (r.id, r.full_name);
    END LOOP;
END;
$$;


-- P3 · a direct UPDATE vs PREPARE + EXECUTE
-- @pair P3 A
UPDATE core.dim_store AS s SET region = upper(s.region) WHERE s.country_code = 'DE';

PREPARE p3(text) AS
UPDATE core.dim_store AS s SET region = upper(s.region) WHERE s.country_code = $1;

-- @pair P3 B
EXECUTE p3('DE');


-- P4 · ->> vs #>>
-- @pair P4 A
INSERT INTO mart.channel_mix (channel) SELECT o.payload ->> 'src' FROM staging.orders AS o;

-- @pair P4 B
INSERT INTO mart.channel_mix (channel) SELECT o.payload #>> '{src}' FROM staging.orders AS o;


-- P5 · ->> vs jsonb_extract_path_text
-- @pair P5 A
INSERT INTO mart.channel_mix (region) SELECT o.payload ->> 'region' FROM staging.orders AS o;

-- @pair P5 B
INSERT INTO mart.channel_mix (region)
SELECT jsonb_extract_path_text(o.payload, 'region') FROM staging.orders AS o;


-- P6 · CREATE TEMP TABLE AS vs SELECT INTO TEMP
-- @pair P6 A
CREATE TEMP TABLE t6 AS
SELECT p.id, p.method FROM staging.payments AS p WHERE p.status = 'OK';

DROP TABLE t6;

-- @pair P6 B
SELECT p.id, p.method INTO TEMP TABLE t6 FROM staging.payments AS p WHERE p.status = 'OK';


-- =====================================================================================
-- The realistic load: incremental payments, by watermark
-- =====================================================================================
CREATE PROCEDURE mart.pr_load_payments()
LANGUAGE plpgsql AS $$
DECLARE
    v_wm   date;
    v_rows bigint;
BEGIN
-- R1 · the watermark
    SELECT COALESCE(MAX(fp.paid_date), DATE '1900-01-01') INTO v_wm FROM core.fct_payment AS fp;
-- R2 · the incremental load, converted at the day's rate
    INSERT INTO core.fct_payment (payment_id, order_id, method, amount, amount_usd, paid_date, status)
    SELECT p.id, p.order_id, p.method, p.amount, p.amount * fx.rate_to_usd, p.paid_at::date, p.status
    FROM staging.payments AS p
    JOIN staging.fx_rates AS fx ON fx.currency = p.currency AND fx.rate_date = p.paid_at::date
    WHERE p.paid_at::date > v_wm AND p.status <> 'VOID';
    GET DIAGNOSTICS v_rows = ROW_COUNT;
-- R3 · the run log
    INSERT INTO mart.events_log (id, kind, rolled_up) VALUES (v_rows, 'payments', true);
END;
$$;

CALL mart.pr_load_payments();
