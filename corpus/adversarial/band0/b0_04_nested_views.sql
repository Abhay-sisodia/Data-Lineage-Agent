-- Band 0 / views on views on views
-- Why it's hard: depth, plus alias collisions between layers. The same column name
-- means different things at different levels.
-- Approach: recursive expansion with cycle detection and a declared depth cap.
--
-- Expected: lineage resolves through all three layers to the base table columns,
-- not merely to the outermost view.

CREATE OR REPLACE VIEW v_cust_l1 AS
SELECT cust_id      AS id,
       region       AS region,
       last_login   AS login_ts,
       status_code  AS status
  FROM stg_customer;
/

CREATE OR REPLACE VIEW v_cust_l2 AS
SELECT id           AS cust_id,
       region,
       login_ts,
       CASE WHEN status = 1 THEN 1 ELSE 0 END AS status_flag
  FROM v_cust_l1;
/

CREATE OR REPLACE VIEW v_cust_l3 AS
SELECT cust_id,
       region,
       login_ts     AS last_login,
       status_flag
  FROM v_cust_l2;
/

CREATE OR REPLACE PROCEDURE b0_nested_views IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM v_cust_l3
     WHERE status_flag = 1;

    COMMIT;
END b0_nested_views;
/
