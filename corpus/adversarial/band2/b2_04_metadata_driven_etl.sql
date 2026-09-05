-- Band 2 / metadata-driven ETL
-- Why it's hard: THE PIPELINE IS NOT IN THE CODE. It lives in a table. The procedure
-- below is a generic engine roughly forty lines long that could move any column
-- between any two tables, and reading it tells you almost nothing about what the
-- system does.
-- Approach: ingest the config table as a first-class source artifact and re-read it
-- on every sync.
--
-- Described in the spike document as the worst case in the whole corpus, and it earns
-- that. The flag it must raise is loud: the pipeline can change with NO code change,
-- no commit, and no diff - someone updates a row and the lineage is different. Any
-- lineage claim derived from it is only valid as at the config version that produced
-- it, which is a strong argument for pinning config into the run record.

CREATE TABLE etl_config (
    mapping_id    NUMBER(6)     NOT NULL,
    source_table  VARCHAR2(30)  NOT NULL,
    source_column VARCHAR2(30)  NOT NULL,
    target_table  VARCHAR2(30)  NOT NULL,
    target_column VARCHAR2(30)  NOT NULL,
    filter_clause VARCHAR2(200),
    is_enabled    NUMBER(1)     DEFAULT 1,
    CONSTRAINT pk_etl_config PRIMARY KEY (mapping_id)
);
/

INSERT INTO etl_config VALUES (1, 'STG_CUSTOMER', 'CUST_ID',    'TMP_RECENT',   'CUST_ID',    NULL, 1);
INSERT INTO etl_config VALUES (2, 'STG_CUSTOMER', 'LAST_LOGIN', 'TMP_RECENT',   'LAST_LOGIN', 'last_login IS NOT NULL', 1);
INSERT INTO etl_config VALUES (3, 'STG_CUSTOMER', 'EMAIL',      'DIM_CUSTOMER', 'EMAIL',      NULL, 1);
COMMIT;
/

CREATE OR REPLACE PROCEDURE b2_metadata_driven_etl IS
    v_sql VARCHAR2(1000);
BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('LINEAGE_CORPUS', 'b2_metadata_driven_etl');

    FOR cfg IN (SELECT source_table, source_column,
                       target_table, target_column, filter_clause
                  FROM etl_config
                 WHERE is_enabled = 1
                 ORDER BY mapping_id)
    LOOP
        v_sql := 'INSERT INTO ' || cfg.target_table || ' (' || cfg.target_column || ') '
              || 'SELECT ' || cfg.source_column || ' FROM ' || cfg.source_table;

        IF cfg.filter_clause IS NOT NULL THEN
            v_sql := v_sql || ' WHERE ' || cfg.filter_clause;
        END IF;

        BEGIN
            EXECUTE IMMEDIATE v_sql;
        EXCEPTION
            WHEN OTHERS THEN NULL;  -- not-null targets will reject; not the point
        END;
    END LOOP;

    COMMIT;
END b2_metadata_driven_etl;
/
