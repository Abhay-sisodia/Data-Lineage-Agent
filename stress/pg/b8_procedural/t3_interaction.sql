-- =====================================================================================
-- B8 · T3 · INTERACTION — do the procedural rules compose?
--
-- Objective card: stress/pg/b8_procedural/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- Conventions: every earlier decision, OBJECTIVES.md rules 1-9, D-39..D-42.
-- =====================================================================================


-- R1 — a loop whose body runs constant dynamic SQL, the record passed by USING
CREATE FUNCTION mart.fn_loop_dyn() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    rd record;
BEGIN
    FOR rd IN SELECT o.channel FROM staging.orders AS o WHERE o.status = 'PAID'
    LOOP
-- S1 · constant text in a loop; USING values are external (D-42)
        EXECUTE 'INSERT INTO mart.kpi_rollup (level, segment) SELECT $1, c.segment FROM staging.customers AS c' USING rd.channel;
    END LOOP;
END;
$$;


-- R2 — a trigger with WHEN, whose body calls a scalar function
CREATE FUNCTION mart.fn_store_region(p_store integer) RETURNS text
LANGUAGE plpgsql AS $$
DECLARE
    v_region text;
BEGIN
-- S2 · the scalar function's SELECT INTO
    SELECT s.region INTO v_region FROM core.dim_store AS s WHERE s.id = p_store;
    RETURN v_region;
END;
$$;

CREATE FUNCTION mart.trg_big_order() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_t3 integer;
BEGIN
-- S3 · the trigger body: NEW columns, and a scalar function of NEW.store_id
    INSERT INTO mart.channel_mix (channel, region, revenue)
    VALUES (NEW.channel, mart.fn_store_region(NEW.store_id), NEW.amount);
    RETURN NEW;
END;
$$;

CREATE TRIGGER t_big_order
AFTER INSERT ON staging.orders
FOR EACH ROW WHEN (NEW.amount > 1000)
EXECUTE FUNCTION mart.trg_big_order();


-- R4 — a RETURN QUERY function, read under GROUP BY
CREATE FUNCTION mart.fn_lines(p_status text)
RETURNS TABLE (pid bigint, qty integer, price numeric)
LANGUAGE plpgsql AS $$
DECLARE
    v_r4 integer;
BEGIN
    RETURN QUERY SELECT ol.product_id, ol.quantity, ol.unit_price FROM staging.order_lines AS ol WHERE ol.status = p_status;
END;
$$;

-- S4 · the read, grouped
INSERT INTO mart.product_performance (product_id, units, revenue)
SELECT f.pid, SUM(f.qty), SUM(f.qty * f.price)
FROM mart.fn_lines('SHIPPED') AS f
GROUP BY f.pid;


-- R5 — a loop over a RETURN QUERY function
CREATE FUNCTION mart.fn_loop_over_fn() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    rf record;
BEGIN
    FOR rf IN SELECT f.pid, f.price FROM mart.fn_lines('OPEN') AS f
    LOOP
-- S5 · the record traced through the function into its body
        UPDATE core.dim_product AS dp
        SET list_price = rf.price
        WHERE dp.product_id = rf.pid;
    END LOOP;
END;
$$;


-- R6 — a SELECT INTO variable feeding an UPDATE's WHERE inside a loop
CREATE FUNCTION mart.fn_threshold_loop() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    v_avg numeric;
    rw    record;
BEGIN
-- S6 · the threshold
    SELECT AVG(p.amount) INTO v_avg FROM staging.payments AS p;
    FOR rw IN SELECT w.id, w.capacity FROM core.dim_warehouse AS w
    LOOP
-- S7 · in the loop: the variable in WHERE, the record in SET
        UPDATE mart.carrier_scorecard AS cs
        SET shipments = rw.capacity
        WHERE cs.carrier_id = rw.id AND cs.cost_per_kg > v_avg;
    END LOOP;
END;
$$;
