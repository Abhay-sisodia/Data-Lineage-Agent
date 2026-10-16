-- =====================================================================================
-- B11 · T2 · SCD2 — a slowly-changing customer dimension, loaded and published
--
-- Objective card: stress/pg/b11_integration/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17. Assumes a sequence
--                 core.dim_customer_seq exists (no lineage).
--
-- A temp table of changed customers (chosen by a watermark, D-43), the close-out
-- UPDATE ... FROM, the insert of new versions with a lookup subquery (D-12, D-13), and a
-- PL/pgSQL procedure that MERGEs the mart and logs per region in a loop (D-39). Every
-- earlier decision applies.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · the changed customers: active, and newer than the dimension's latest version
-- -------------------------------------------------------------------------------------
CREATE TEMP TABLE chg AS
SELECT c.id, c.email, c.full_name, c.country_code, c.segment
FROM staging.customers AS c
WHERE c.status = 'A'
  AND c.created_at > (SELECT MAX(d.valid_from) FROM core.dim_customer AS d);


-- -------------------------------------------------------------------------------------
-- S2 · close out their current versions
-- -------------------------------------------------------------------------------------
UPDATE core.dim_customer AS d
SET valid_to = current_date,
    is_current = false
FROM chg AS c
WHERE d.customer_id = c.id
  AND d.is_current;


-- -------------------------------------------------------------------------------------
-- S3 · insert the new versions, the region looked up from the earliest store in the country
-- -------------------------------------------------------------------------------------
INSERT INTO core.dim_customer
    (customer_key, customer_id, email, full_name, country_code, region, segment, status,
     valid_from, valid_to, is_current)
SELECT nextval('core.dim_customer_seq'),
       c.id,
       lower(c.email),
       c.full_name,
       c.country_code,
       (SELECT s.region
        FROM core.dim_store AS s
        WHERE s.country_code = c.country_code
        ORDER BY s.opened_on
        LIMIT 1),
       c.segment,
       'A',
       current_date,
       DATE '9999-12-31',
       true
FROM chg AS c;


-- R4 — publish the current dimension to the mart, and log per region
CREATE PROCEDURE mart.pr_publish_customers(p_since date)
LANGUAGE plpgsql AS $$
DECLARE
    r record;
BEGIN
-- S4 · MERGE the current versions into the summary; the parameter bounds the window
    MERGE INTO mart.customer_summary AS t
    USING (
        SELECT d.customer_id, d.full_name, d.segment, d.region
        FROM core.dim_customer AS d
        WHERE d.is_current AND d.valid_from >= p_since
    ) AS s
    ON t.customer_id = s.customer_id
    WHEN MATCHED THEN
        UPDATE SET full_name = s.full_name, segment = s.segment, region = s.region
    WHEN NOT MATCHED THEN
        INSERT (customer_id, full_name, segment, region)
        VALUES (s.customer_id, s.full_name, s.segment, s.region);
    FOR r IN SELECT d.region, COUNT(*) AS n
             FROM core.dim_customer AS d
             WHERE d.is_current
             GROUP BY d.region
    LOOP
-- S5 · one log row per region, from the loop record
        INSERT INTO mart.kpi_rollup (level, region, customers)
        VALUES ('region', r.region, r.n);
    END LOOP;
END;
$$;

-- S6 · the nightly call: counted, no edges of its own (D-39)
CALL mart.pr_publish_customers(current_date - 1);
