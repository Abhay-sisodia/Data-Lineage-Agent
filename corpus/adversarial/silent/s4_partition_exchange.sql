-- SILENT FAILURE 4 / partition exchange
--
-- ALTER TABLE ... EXCHANGE PARTITION moves an ENTIRE DATASET into a table with no
-- INSERT anywhere. Analyse DML only and you report that the table has no writer -
-- while in reality every row in it arrived this way.
--
-- Defence: treat DDL in the log as lineage-bearing, not just DML.
--
-- Expected: an edge from fct_revenue_stage to fct_revenue_part, mechanism DDL.
-- A report of "no writer" is the failure this case detects.

CREATE TABLE fct_revenue_part (
    cust_id      NUMBER(12),
    period_month DATE,
    net_amount   NUMBER(14,2),
    order_count  NUMBER(8)
)
-- NOTE: TO_DATE rather than the ANSI literal DATE '2026-01-01'. Both are valid Oracle,
-- but the grammar's partition-bound rule rejects the ANSI form - see
-- docs/grammar_limitations.md GL-001. Written this way so that THIS file tests the
-- construct it exists for (partition exchange) rather than failing on date syntax.
PARTITION BY RANGE (period_month) (
    PARTITION p_2025 VALUES LESS THAN (TO_DATE('2026-01-01', 'YYYY-MM-DD')),
    PARTITION p_2026 VALUES LESS THAN (TO_DATE('2027-01-01', 'YYYY-MM-DD'))
);
/

CREATE TABLE fct_revenue_stage (
    cust_id      NUMBER(12),
    period_month DATE,
    net_amount   NUMBER(14,2),
    order_count  NUMBER(8)
);
/

CREATE OR REPLACE PROCEDURE s4_partition_exchange IS
BEGIN
    DBMS_APPLICATION_INFO.SET_MODULE('LINEAGE_CORPUS', 's4_partition_exchange');

    -- A perfectly ordinary load into the staging table.
    INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
    SELECT cust_id,
           TRUNC(order_date, 'MM'),
           gross_amount - discount_amt,
           1
      FROM stg_orders;

    COMMIT;

    -- And now the whole dataset moves, with no INSERT into the target at all.
    EXECUTE IMMEDIATE
        'ALTER TABLE fct_revenue_part EXCHANGE PARTITION p_2026 '
     || 'WITH TABLE fct_revenue_stage WITHOUT VALIDATION';
END s4_partition_exchange;
/
