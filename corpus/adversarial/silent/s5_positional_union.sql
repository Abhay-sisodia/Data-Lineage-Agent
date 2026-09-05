-- SILENT FAILURE 5 / UNION binds by position, not by name
--
-- Arm one selects (cust_id, region). Arm two selects (region, cust_id). SQL binds
-- these POSITIONALLY: arm two's region feeds the first output column. Match on name
-- instead and you wire the wrong sources together - silently, with a result that
-- looks entirely reasonable.
--
-- Defence: implement positional binding per SQL semantics, and flag any arm whose
-- column names disagree with the first arm as a review candidate.
--
-- Correct (positional):  col_a <- {stg_customer.cust_id, stg_orders.order_id}
-- Wrong (name-matched):  col_a <- {stg_customer.cust_id, stg_orders.order_date}
--
-- The types are deliberately compatible in POSITION order (NUMBER, DATE) so that
-- Oracle accepts the statement. That is what makes this a silent failure rather than
-- a loud one: it compiles, it runs, it produces plausible output, and only the
-- lineage is wrong. An earlier version of this case reversed the types too and was
-- simply rejected by the compiler - which would have tested nothing.

CREATE OR REPLACE PROCEDURE s5_positional_union IS
BEGIN
    INSERT INTO tmp_recent (cust_id, last_login)
    SELECT col_a, col_b
      FROM (
            -- Arm 1: aliases agree with position. (NUMBER, DATE)
            SELECT cust_id     AS col_a,
                   last_login  AS col_b
              FROM stg_customer
            UNION ALL
            -- Arm 2: aliases DISAGREE with position, types still line up. (NUMBER, DATE)
            -- Position says col_a <- order_id. The alias says col_b. SQL follows position.
            SELECT order_id    AS col_b,
                   order_date  AS col_a
              FROM stg_orders
           );

    COMMIT;
END s5_positional_union;
/
