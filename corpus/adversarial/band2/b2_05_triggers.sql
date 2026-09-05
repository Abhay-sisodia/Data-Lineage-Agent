-- Band 2 / triggers
-- Why it's hard: they fire invisibly. The calling code shows a write to ONE table;
-- a second table is written as a side effect, with nothing at the call site to
-- suggest it.
-- Approach: parse triggers separately and attach them to the TABLE, not the caller.
-- Any statement touching that table then inherits the trigger's edges.
--
-- The failure mode if you get this wrong is a silent one: dim_customer.lifetime_value
-- appears to have no writer at all, because its only writer is a trigger nobody
-- connected to anything.

CREATE OR REPLACE TRIGGER trg_recent_audit
    AFTER INSERT ON tmp_recent
    FOR EACH ROW
BEGIN
    -- Writes a different table entirely, from the inserted row's values.
    UPDATE dim_customer
       SET lifetime_value = NVL(lifetime_value, 0) + 1
     WHERE cust_id = :NEW.cust_id;
END;
/

CREATE OR REPLACE TRIGGER trg_customer_default
    BEFORE INSERT ON dim_customer
    FOR EACH ROW
BEGIN
    -- Derives one column from another before the row lands. The edge
    -- dim_customer.region -> dim_customer.is_active exists ONLY here.
    IF :NEW.is_active IS NULL THEN
        :NEW.is_active := CASE WHEN :NEW.region = 'EU' THEN 1 ELSE 0 END;
    END IF;
END;
/

-- The caller. Nothing here hints that two other columns get written.
CREATE OR REPLACE PROCEDURE b2_triggers IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login FROM stg_customer WHERE status_code = 1;

    COMMIT;
END b2_triggers;
/
