-- SILENT FAILURE 6 / updatable views and INSTEAD OF triggers
--
-- You write to a view. The real write lands on a base table you never connected to
-- anything. Report the view as the target and the actual regulated table appears to
-- have no writer.
--
-- Defence: resolve views to base tables at ingest; parse INSTEAD OF triggers
-- separately and attach them to the object rather than the caller.
--
-- Correct: s6_write_through_view writes dim_customer.is_active AND
--          dw_audit_log.changed_by, via the INSTEAD OF trigger
-- Wrong:   writes v_customer_editable.is_active - an object that stores nothing

CREATE TABLE dw_audit_log (
    cust_id    NUMBER(12),
    changed_by VARCHAR2(30),
    changed_at DATE
);
/

CREATE OR REPLACE VIEW v_customer_editable AS
SELECT d.cust_id,
       d.region,
       d.is_active,
       s.email AS source_email
  FROM dim_customer d
  JOIN stg_customer s ON s.cust_id = d.cust_id;
/

-- A join view is not directly updatable, so the write is redirected here.
CREATE OR REPLACE TRIGGER trg_customer_editable
    INSTEAD OF UPDATE ON v_customer_editable
    FOR EACH ROW
BEGIN
    UPDATE dim_customer
       SET is_active = :NEW.is_active
     WHERE cust_id = :NEW.cust_id;

    INSERT INTO dw_audit_log (cust_id, changed_by, changed_at)
    VALUES (:NEW.cust_id, USER, SYSDATE);
END;
/

CREATE OR REPLACE PROCEDURE s6_write_through_view IS
BEGIN
    -- Looks like a write to a view. Actually writes two base tables.
    UPDATE v_customer_editable
       SET is_active = 1
     WHERE region = 'EU';

    COMMIT;
END s6_write_through_view;
/
