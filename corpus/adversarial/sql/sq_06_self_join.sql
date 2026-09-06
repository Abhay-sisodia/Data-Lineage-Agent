-- COMPLEX SQL 6 / self-join and a scalar subquery in the select list
--
-- Why it's hard: the SAME table appears twice under different aliases, so
-- `h.parent_cust_id` and `parent.cust_id` are different columns of the same relation.
-- Resolving by table name alone fuses the two sides and produces a self-edge that does
-- not exist. This is the join-side analogue of the shared temp table problem.
--
-- The scalar subquery in the select list adds a second difficulty: its own WHERE
-- correlates back to the outer query, so its predicate columns are filter influence
-- rather than value sources.

CREATE OR REPLACE PROCEDURE sq_self_join IS
BEGIN
    INSERT INTO dim_customer_hier (cust_id, parent_cust_id, depth, root_name)
    SELECT child.cust_id,
           parent.cust_id,
           NVL(parent.depth, 0) + 1,
           (SELECT MAX(s.email)
              FROM stg_customer s
             WHERE s.cust_id = parent.cust_id)
      FROM dim_customer_hier child
      JOIN dim_customer_hier parent
        ON parent.cust_id = child.parent_cust_id
     WHERE child.depth < 5;

    COMMIT;
END sq_self_join;
/
