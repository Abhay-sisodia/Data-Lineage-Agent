-- SILENT FAILURE 2 / schema context
--
-- An unqualified name resolves through the EXECUTING USER'S schema. Two jobs running
-- identical code against different schemas touch different tables. Bind against one
-- global default and half your lineage is attributed to the wrong objects.
--
-- Defence: bind names per execution context, using the log's parsing schema rather
-- than a single assumed default. The parsing_schema_name column in V$SQL is the
-- evidence that makes this resolvable at all.
--
-- PARSE-ONLY for the second half: reporting.stg_customer is a schema we deliberately
-- do not create, so it stands in for the "not granted" case and must produce a
-- counted boundary rather than an error.

CREATE OR REPLACE PROCEDURE s2_schema_context IS
BEGIN
    -- Unqualified: resolves through whichever schema executes this.
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM stg_customer
     WHERE last_login IS NOT NULL;

    -- Explicitly qualified against a schema that may not be in scope.
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT cust_id, last_login
      FROM reporting.stg_customer
     WHERE last_login IS NOT NULL;

    COMMIT;
END s2_schema_context;
/
