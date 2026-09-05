-- SILENT FAILURE 1 / synonyms
--
-- The code says dim_customer. The synonym points somewhere else entirely. The parser
-- succeeds, binds the literal name, and reports the WRONG OBJECT at Tier B - with all
-- three witnesses agreeing, because the logs and the profile describe the same wrong
-- name too.
--
-- This is the case that proves evidence triangulation does not protect against
-- name-resolution error. The defence has to be at ingest: resolve every name against
-- ALL_SYNONYMS / ALL_OBJECTS and store the resolution as evidence.
--
-- Correct answer: writes DW_DIM_CUSTOMER_V2.is_active
-- Wrong answer that looks identical: writes DIM_CUSTOMER.is_active

CREATE TABLE dw_dim_customer_v2 (
    cust_id        NUMBER(12) NOT NULL,
    region         VARCHAR2(10),
    is_active      NUMBER(1),
    email          VARCHAR2(200),
    lifetime_value NUMBER(14,2)
);
/

-- The redirect. Nothing in the procedure below reveals this exists.
CREATE OR REPLACE SYNONYM customer_target FOR dw_dim_customer_v2;
/

CREATE OR REPLACE PROCEDURE s1_synonym_redirect IS
BEGIN
    -- Looks like an ordinary write to a table called customer_target.
    -- Actually writes dw_dim_customer_v2.
    UPDATE customer_target
       SET is_active = 1
     WHERE cust_id IN (SELECT cust_id FROM tmp_recent);

    COMMIT;
END s1_synonym_redirect;
/
