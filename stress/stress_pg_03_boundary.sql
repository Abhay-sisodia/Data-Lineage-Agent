-- STRESS PG 3 / the boundary, and whether a refusal stays where it is put
--
-- The other two packages test the INSIDE of docs/postgres_scope.md. This one tests the edge
-- of it, and it tests something neither of them can: that a refused statement does not
-- poison the statements around it.
--
-- WHY THAT IS A SEPARATE QUESTION. A refusal is only survivable if it is LOCAL. An analyser
-- that refuses a PL/pgSQL body is behaving correctly; one that refuses the body and then
-- loses the ordinary INSERT three lines below it has converted one known gap into an
-- unknown number of them, and the parse-coverage figure will not show the difference. The
-- Oracle side learned this the hard way: `$IF $PLSQL_UNIT` poisons the statements AROUND it
-- rather than itself, which is why `conditional_compilation_spans` exists at all - and the
-- PostgreSQL routine-body pattern was found OVER-BROAD during the port, spanning 2561
-- characters and taking Oracle's parse coverage from 81.6% to 80.0% before it was anchored.
--
-- So the statements here ALTERNATE: refused, in-scope, refused, in-scope. Every in-scope
-- statement is surrounded by out-of-scope ones, and the key states edges for all of them.
-- A miss on an in-scope statement here, when the identical statement passes in
-- stress_pg_01, IS the containment failure and nothing else.
--
-- EVERY REFUSAL BELOW IS NAMED IN ADVANCE, with the code the scope document already gives
-- it. That is the user-facing promise being tested: refusals are predictable. If a
-- statement here is refused for a DIFFERENT reason than the key says, the register gets a
-- finding even though the count came out right.
--
-- Ground truth was written from this source BEFORE the analyser was run over it.

-- ====================================================================================
-- 1. OUT OF SCOPE - a PL/pgSQL routine body.  UNSUPPORTED_CONSTRUCT.
--
--    The body is a dollar-quoted STRING LITERAL, not part of the SQL parse tree, and no
--    PL/pgSQL grammar has been chosen (ADR-0002 section 5). The INSERT inside it is
--    perfectly ordinary SQL and is invisible for exactly that reason - which is the cost
--    of the parked decision, made concrete.
-- ====================================================================================
CREATE OR REPLACE FUNCTION refresh_revenue() RETURNS void AS $$
BEGIN
    INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
    SELECT o.cust_id, date_trunc('month', o.order_date), SUM(o.gross_amount), COUNT(*)
      FROM stg_orders o
     GROUP BY o.cust_id, date_trunc('month', o.order_date);
END;
$$ LANGUAGE plpgsql;

-- ====================================================================================
-- 2. IN SCOPE. Immediately after a dollar-quoted body, which is where an over-broad
--    routine-body pattern would swallow it.
-- ====================================================================================
INSERT INTO gtt_stage (cust_id, period_month, amount)
SELECT o.cust_id, date_trunc('month', o.order_date), SUM(o.gross_amount)
  FROM stg_orders o
 WHERE o.currency = 'GBP'
 GROUP BY o.cust_id, date_trunc('month', o.order_date);

-- ====================================================================================
-- 3. OUT OF SCOPE - an anonymous DO block.  UNSUPPORTED_CONSTRUCT.
--    The same dollar-quoted body, without even a name to attribute it to.
-- ====================================================================================
DO $$
BEGIN
    UPDATE dim_customer SET lifetime_value = 0;
END
$$;

-- ====================================================================================
-- 4. IN SCOPE, and deliberately the construct most likely to be mistaken for a refusal:
--    a table called `truncate_log` and a column called `copy_flag`. The TRUNCATE and COPY
--    refusals are matched by PATTERN, and a pattern that fires on a NAME rather than on a
--    statement would refuse this.
--
--    (Both identifiers are inside string literals and aliases here rather than real
--    objects, so the dictionary is unchanged.)
-- ====================================================================================
INSERT INTO audit_log (cust_id, changed_by, detail)
SELECT c.cust_id, 'truncate_log', c.email
  FROM stg_customer c
 WHERE c.status_code = 'A';

-- ====================================================================================
-- 5. OUT OF SCOPE - COPY.  SOURCE_UNAVAILABLE, and a DIFFERENT code from the others,
--    because the reason is different in kind: the rows come from outside the database
--    entirely, and no grammar decision would ever make them visible.
-- ====================================================================================
COPY stg_orders (order_id, cust_id, gross_amount) FROM '/var/load/orders.csv' WITH (FORMAT csv);

-- ====================================================================================
-- 6. IN SCOPE. After a COPY, and writing the very table the COPY loaded - so a reader can
--    see both facts about stg_orders side by side: one write refused with a stated reason,
--    one read traced.
-- ====================================================================================
INSERT INTO fct_revenue_stage (cust_id, period_month, net_amount, order_count)
SELECT o.cust_id, date_trunc('month', o.order_date), SUM(o.gross_amount), COUNT(*)
  FROM stg_orders o
 WHERE o.status_code = 'OK'
 GROUP BY o.cust_id, date_trunc('month', o.order_date);

-- ====================================================================================
-- 7. OUT OF SCOPE - CREATE TRIGGER.  UNSUPPORTED_CONSTRUCT.
--
--    A PostgreSQL trigger NAMES a function that holds the body, so the lineage is in a
--    routine we cannot parse. ORACLE TRIGGERS ARE HANDLED AND DELIBERATELY NOT REFUSED,
--    which is the entire reason `Construct` carries a `dialects` field.
-- ====================================================================================
CREATE TRIGGER trg_customer_audit
    AFTER INSERT ON dim_customer
    FOR EACH ROW
    EXECUTE FUNCTION log_customer_change();

-- ====================================================================================
-- 8. OUT OF SCOPE - TRUNCATE.  UNSUPPORTED_CONSTRUCT.
--
--    No column has a source to name - the same shape as a DELETE with no predicate - but
--    the statement decides what the table contains afterwards, so it is RECORDED rather
--    than skipped. Skipping it is how a table acquires its contents with no writer.
-- ====================================================================================
TRUNCATE TABLE gtt_stage;

-- ====================================================================================
-- 9. IN SCOPE, last, and after four consecutive out-of-scope statements. If containment
--    fails anywhere it fails here.
-- ====================================================================================
UPDATE dim_customer d
   SET is_active = CASE WHEN s.status_code = 'A' THEN true ELSE false END
  FROM stg_customer s
 WHERE s.cust_id = d.cust_id;
