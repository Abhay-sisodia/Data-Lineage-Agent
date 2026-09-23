"""What PostgreSQL constructs the analyser handles, and what it refuses.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe scripts\\probe_postgres.py

**This produces the SCOPE, it does not assume it.** The point of a scope contract is that
inside it nothing fails and outside it everything is refused for a reason stated in advance.
A scope written from opinion is worth nothing; this measures one, construct by construct, so
the stress packages that follow can be built entirely inside it.

Five buckets, mirroring `probe_analyser.py`, and only the last two are defects:

  EDGES    at least one edge came out. NOT a claim the edges are CORRECT - only a
           ground-truth key proves that. This bucket says "in scope", nothing more.
  REFUSED  no edge, and the analyser refused. Honest, counted, survivable, and the
           correct answer for anything outside the scope.
  DECLARED no edge and no refusal, but a boundary NAMES what stopped it.
  NO-SOURCE the statement was SEEN and counted, and has no upstream to name. `INSERT INTO
           t VALUES (1, 2)` is the case: every value is a literal, so there is nothing to
           trace, and that is a complete answer rather than a missing one - the same
           reading `_analyse_delete` gives a DELETE with no predicate.
  SILENT   no edge, no refusal, nothing declared, AND THE STATEMENT WAS NEVER COUNTED.
           The failure this product exists to prevent, and the only outcome that is never
           acceptable, in scope or out.

**The NO-SOURCE bucket was added after the first run over-reported**, exactly as the Oracle
probe's DECLARED bucket was. Two all-literal writes came back SILENT while both were in
fact analysed and correctly found nothing. An instrument that cannot tell "counted and
empty" from "skipped" is measuring the wrong thing here, where the whole question is
whether anything falls through unowned.
  CRASH    an exception escaped `analyse_source`. Worse than silent: the whole file is
           lost and nothing records that it happened.

WHAT A RESULT HERE IS WORTH. A SILENT or CRASH row is a HYPOTHESIS. The Oracle probe's
first run produced six silent rows and four of them were the probe rather than the
analyser. Re-test each on its own, varying one thing, before writing anything down.

The dictionary is synthetic and deliberately generous, so `NAME_NOT_RESOLVED` never becomes
the answer. This probe is about construct handling, not name resolution.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from lineage.analysis.procedure import analyse_source  # noqa: E402
from lineage.config import AnalysisConfig  # noqa: E402
from lineage.ir.model import BoundaryKind  # noqa: E402
from lineage.resolution.dictionary import Dictionary  # noqa: E402

SCHEMA = "public"
COLUMNS = {
    f"{SCHEMA}.stg_orders": [
        "order_id", "cust_id", "order_date", "gross_amount", "discount_amt", "currency", "payload"
    ],
    f"{SCHEMA}.stg_order_lines": ["line_id", "order_id", "product_id", "quantity", "unit_price"],
    f"{SCHEMA}.stg_customer": ["cust_id", "region", "email", "signup_date", "status_code"],
    f"{SCHEMA}.dim_customer": ["cust_id", "region", "is_active", "lifetime_value"],
    f"{SCHEMA}.dim_hier": ["cust_id", "parent_cust_id", "depth", "root_name"],
    f"{SCHEMA}.fct_revenue": ["cust_id", "period_month", "net_amount", "order_count"],
    f"{SCHEMA}.fct_stage": ["cust_id", "period_month", "net_amount", "order_count"],
    f"{SCHEMA}.gtt_stage": ["cust_id", "amount", "note"],
    f"{SCHEMA}.audit_log": ["cust_id", "changed_by", "changed_at"],
}

# Constructs, grouped by what we expect the scope to contain. The expectation is recorded
# so the probe reports AGREEMENT or SURPRISE rather than a bare bucket - a construct that
# lands where predicted is evidence; one that does not is the finding.
IN_SCOPE = "in"        # must produce edges
NO_LINEAGE = "none"    # in scope, counted, and correctly has nothing to trace
OUT_OF_SCOPE = "out"   # must be refused or declared, never silent

CONSTRUCTS: list[tuple[str, str, str]] = [
    # ---- core DML -------------------------------------------------------------------
    (IN_SCOPE, "INSERT ... SELECT", """
        INSERT INTO fct_revenue (cust_id, period_month, net_amount, order_count)
        SELECT o.cust_id, date_trunc('month', o.order_date), SUM(o.gross_amount), COUNT(*)
          FROM stg_orders o GROUP BY o.cust_id, date_trunc('month', o.order_date);"""),
    (NO_LINEAGE, "INSERT ... VALUES, all literals", """
        INSERT INTO gtt_stage (cust_id, amount) VALUES (1, 2);"""),
    (IN_SCOPE, "INSERT with a leading CTE", """
        INSERT INTO gtt_stage (cust_id, amount)
        WITH src AS (SELECT o.cust_id AS cust_id, o.gross_amount AS amt FROM stg_orders o)
        SELECT s.cust_id, s.amt FROM src s;"""),
    (IN_SCOPE, "UPDATE ... SET from a column", """
        UPDATE fct_revenue SET net_amount = net_amount * 1.05 WHERE cust_id > 0;"""),
    (IN_SCOPE, "UPDATE ... FROM (PostgreSQL join-update)", """
        UPDATE fct_revenue f SET net_amount = s.gross_amount
          FROM stg_orders s WHERE s.cust_id = f.cust_id;"""),
    (IN_SCOPE, "DELETE ... WHERE subquery", """
        DELETE FROM gtt_stage WHERE cust_id IN (SELECT o.cust_id FROM stg_orders o);"""),
    (IN_SCOPE, "DELETE ... USING", """
        DELETE FROM gtt_stage g USING stg_orders o WHERE o.cust_id = g.cust_id;"""),
    (IN_SCOPE, "INSERT ... ON CONFLICT DO UPDATE (upsert)", """
        INSERT INTO fct_revenue (cust_id, net_amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o
        ON CONFLICT (cust_id) DO UPDATE SET net_amount = EXCLUDED.net_amount;"""),
    (IN_SCOPE, "INSERT ... ON CONFLICT DO NOTHING", """
        INSERT INTO fct_revenue (cust_id, net_amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o ON CONFLICT DO NOTHING;"""),
    (IN_SCOPE, "INSERT ... RETURNING", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o RETURNING cust_id;"""),
    (IN_SCOPE, "MERGE (PostgreSQL 15+)", """
        MERGE INTO fct_revenue f USING stg_orders o ON f.cust_id = o.cust_id
        WHEN MATCHED THEN UPDATE SET net_amount = o.gross_amount
        WHEN NOT MATCHED THEN INSERT (cust_id, net_amount) VALUES (o.cust_id, o.gross_amount);"""),
    (IN_SCOPE, "CREATE TABLE AS SELECT", """
        CREATE TABLE fct_stage AS SELECT o.cust_id, o.gross_amount FROM stg_orders o;"""),


    # ---- query shape ----------------------------------------------------------------
    (IN_SCOPE, "chained CTEs, three deep", """
        INSERT INTO gtt_stage (cust_id, amount)
        WITH a AS (SELECT o.cust_id AS cust_id, o.gross_amount AS amt FROM stg_orders o),
             b AS (SELECT a.cust_id AS cust_id, a.amt AS amt FROM a),
             c AS (SELECT b.cust_id AS cust_id, SUM(b.amt) AS amt FROM b GROUP BY b.cust_id)
        SELECT c.cust_id, c.amt FROM c;"""),
    (IN_SCOPE, "WITH RECURSIVE", """
        INSERT INTO gtt_stage (cust_id, amount)
        WITH RECURSIVE tree AS (
            SELECT h.cust_id AS cust_id, 0 AS depth FROM dim_hier h WHERE h.parent_cust_id IS NULL
            UNION ALL
            SELECT h.cust_id, t.depth + 1
              FROM dim_hier h JOIN tree t ON t.cust_id = h.parent_cust_id)
        SELECT t.cust_id, t.depth FROM tree t;"""),
    (IN_SCOPE, "LATERAL join", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, l.total FROM stg_orders o
        CROSS JOIN LATERAL (SELECT SUM(x.unit_price) AS total FROM stg_order_lines x
                             WHERE x.order_id = o.order_id) l;"""),
    (IN_SCOPE, "FULL OUTER JOIN", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT COALESCE(o.cust_id, c.cust_id), o.gross_amount
          FROM stg_orders o FULL OUTER JOIN stg_customer c ON c.cust_id = o.cust_id;"""),
    (IN_SCOPE, "window functions with a frame", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id,
               SUM(o.gross_amount) OVER (PARTITION BY o.cust_id ORDER BY o.order_date
                                         ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)
          FROM stg_orders o;"""),
    (IN_SCOPE, "LAG / LEAD", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, LAG(o.gross_amount, 1, 0) OVER (PARTITION BY o.cust_id
                                                          ORDER BY o.order_date)
          FROM stg_orders o;"""),
    (IN_SCOPE, "GROUPING SETS / ROLLUP", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, SUM(o.gross_amount) FROM stg_orders o
         GROUP BY ROLLUP (o.cust_id, o.currency);"""),
    (IN_SCOPE, "HAVING", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, SUM(o.gross_amount) FROM stg_orders o
         GROUP BY o.cust_id HAVING SUM(o.gross_amount) > 0;"""),
    (IN_SCOPE, "DISTINCT ON (PostgreSQL)", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT DISTINCT ON (o.cust_id) o.cust_id, o.gross_amount
          FROM stg_orders o ORDER BY o.cust_id, o.order_date DESC;"""),
    (IN_SCOPE, "aggregate FILTER (WHERE ...) (PostgreSQL)", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, SUM(o.gross_amount) FILTER (WHERE o.currency = 'GBP')
          FROM stg_orders o GROUP BY o.cust_id;"""),
    (IN_SCOPE, "string_agg with ORDER BY inside", """
        INSERT INTO gtt_stage (cust_id, note)
        SELECT c.cust_id, string_agg(c.region, ',' ORDER BY c.region) FROM stg_customer c
         GROUP BY c.cust_id;"""),
    (IN_SCOPE, "scalar subquery in the select list", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, (SELECT MAX(x.unit_price) FROM stg_order_lines x
                            WHERE x.order_id = o.order_id) FROM stg_orders o;"""),
    (IN_SCOPE, "EXISTS / NOT EXISTS", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o
         WHERE EXISTS (SELECT 1 FROM stg_customer c WHERE c.cust_id = o.cust_id)
           AND NOT EXISTS (SELECT 1 FROM dim_customer d WHERE d.cust_id = o.cust_id);"""),
    (IN_SCOPE, "UNION ALL / EXCEPT", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o
        UNION ALL
        SELECT c.cust_id, 0 FROM stg_customer c
        EXCEPT
        SELECT d.cust_id, 0 FROM dim_customer d;"""),
    (IN_SCOPE, "cast with :: and CASE", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id::int,
               CASE WHEN o.currency = 'GBP' THEN o.gross_amount ELSE 0 END
          FROM stg_orders o;"""),
    (IN_SCOPE, "date_trunc / EXTRACT / INTERVAL", """
        INSERT INTO fct_revenue (cust_id, period_month, net_amount)
        SELECT o.cust_id, date_trunc('month', o.order_date),
               EXTRACT(YEAR FROM o.order_date) + o.gross_amount
          FROM stg_orders o WHERE o.order_date > now() - INTERVAL '30 days';"""),
    (IN_SCOPE, "schema-qualified names", """
        INSERT INTO public.gtt_stage (cust_id, amount)
        SELECT o.cust_id, o.gross_amount FROM public.stg_orders o;"""),
    (NO_LINEAGE, "VALUES list as a table source, all literals", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT v.a, v.b FROM (VALUES (1, 2), (3, 4)) AS v(a, b);"""),
    (IN_SCOPE, "VALUES list joined to a real relation", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, v.b FROM stg_orders o CROSS JOIN (VALUES (1, 2)) AS v(a, b);"""),
    (IN_SCOPE, "FETCH FIRST / LIMIT / OFFSET", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, o.gross_amount FROM stg_orders o
         ORDER BY o.gross_amount DESC LIMIT 10 OFFSET 5;"""),

    # ---- PostgreSQL-specific data types and functions --------------------------------
    (IN_SCOPE, "JSON operators -> and ->>", """
        INSERT INTO gtt_stage (cust_id, note)
        SELECT o.cust_id, o.payload ->> 'channel' FROM stg_orders o;"""),
    (IN_SCOPE, "jsonb_build_object", """
        INSERT INTO gtt_stage (cust_id, note)
        SELECT o.cust_id, jsonb_build_object('amt', o.gross_amount)::text FROM stg_orders o;"""),
    (IN_SCOPE, "unnest / array", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, u.v FROM stg_orders o CROSS JOIN unnest(ARRAY[1, 2]) AS u(v);"""),
    (IN_SCOPE, "generate_series", """
        INSERT INTO gtt_stage (cust_id, amount)
        SELECT o.cust_id, g FROM stg_orders o CROSS JOIN generate_series(1, 3) AS g;"""),

    # ---- expected to be OUT of scope -------------------------------------------------
    (OUT_OF_SCOPE, "PL/pgSQL function body", """
        CREATE OR REPLACE FUNCTION recalc(p_id int) RETURNS void AS $$
        BEGIN
          UPDATE fct_revenue SET order_count = 0 WHERE cust_id = p_id;
        END;
        $$ LANGUAGE plpgsql;"""),
    (OUT_OF_SCOPE, "PL/pgSQL body with a $tag$", """
        CREATE FUNCTION f2() RETURNS void AS $fn$
        BEGIN
          INSERT INTO gtt_stage (cust_id, amount) VALUES (1, 2);
        END;
        $fn$ LANGUAGE plpgsql;"""),
    (OUT_OF_SCOPE, "DO anonymous block", """
        DO $$
        BEGIN
          UPDATE fct_revenue SET order_count = 0;
        END;
        $$;"""),
    (OUT_OF_SCOPE, "CREATE TRIGGER", """
        CREATE TRIGGER trg_audit AFTER INSERT ON fct_revenue
        FOR EACH ROW EXECUTE FUNCTION audit_fn();"""),
    (OUT_OF_SCOPE, "TRUNCATE", "TRUNCATE TABLE gtt_stage;"),
    (OUT_OF_SCOPE, "COPY from file", """
        COPY gtt_stage (cust_id, amount) FROM '/tmp/data.csv' WITH (FORMAT csv);"""),
    (OUT_OF_SCOPE, "data-modifying CTE", """
        WITH moved AS (
            DELETE FROM gtt_stage RETURNING cust_id, amount
        )
        INSERT INTO fct_revenue (cust_id, net_amount) SELECT m.cust_id, m.amount FROM moved m;"""),
]

EXPLANATORY = {
    BoundaryKind.DANGLING_REFERENCE,
    BoundaryKind.UNRESOLVED_IDENTIFIER,
    BoundaryKind.PARSE_FAILURE,
    BoundaryKind.DYNAMIC_SQL,
    BoundaryKind.REFUSAL,
    BoundaryKind.DDL_SEMANTICS,
    BoundaryKind.FUSION_HAZARD,
    BoundaryKind.CROSS_UNIT_STATE,
    BoundaryKind.SOURCE_UNAVAILABLE,
}


def dictionary() -> Dictionary:
    return Dictionary(
        captured_at="2026-09-23T00:00:00Z",
        default_schema=SCHEMA,
        dialect="postgres",
        objects={
            name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
            for name in COLUMNS
        },
        columns=COLUMNS,
        temporary={name: False for name in COLUMNS},
    )


def bucket(source: str, book: Dictionary) -> tuple[str, str]:
    try:
        result = analyse_source(source, book, AnalysisConfig(dialect="postgres"))
    except Exception as exc:  # a crash is a result here, not an error to propagate
        return "CRASH", f"{type(exc).__name__}: {str(exc).splitlines()[0][:70]}"

    if result.edges:
        return "EDGES", f"{len(result.edges)} edges"
    if result.refusals:
        return "REFUSED", result.refusals[0].code.value
    explained = [b for b in result.boundaries if b.kind in EXPLANATORY]
    if explained:
        return "DECLARED", explained[0].kind.value
    if result.statements_seen:
        return "NO-SOURCE", f"{result.statements_seen} counted, nothing to trace"
    return "SILENT", "not even counted"


def main() -> int:
    book = dictionary()
    rows: list[tuple[str, str, str, str, bool]] = []

    for expectation, name, sql in CONSTRUCTS:
        verdict, detail = bucket(sql.strip(), book)
        if expectation == IN_SCOPE:
            agreed = verdict == "EDGES"
        elif expectation == NO_LINEAGE:
            agreed = verdict == "NO-SOURCE"
        else:
            agreed = verdict in ("REFUSED", "DECLARED")
        rows.append((expectation, name, verdict, detail, agreed))

    width = max(len(name) for _, name, _, _, _ in rows)
    print(f"{'':3} {'construct':{width}}  {'verdict':9} detail")
    print("-" * (width + 40))
    for expectation, name, verdict, detail, agreed in rows:
        mark = "  " if agreed else "<<"
        tag = {IN_SCOPE: "in ", NO_LINEAGE: "nil", OUT_OF_SCOPE: "out"}[expectation]
        print(f"{mark} {tag} {name:{width}}  {verdict:10} {detail}")

    surprises = [r for r in rows if not r[4]]
    counts: dict[str, int] = {}
    for _, _, verdict, _, _ in rows:
        counts[verdict] = counts.get(verdict, 0) + 1

    print(f"\n{len(rows)} constructs: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"agreed with expectation: {len(rows) - len(surprises)} / {len(rows)}")
    if surprises:
        print("\nSURPRISES - each is a hypothesis, to be re-tested on its own before"
              " anything is written down:")
        for expectation, name, verdict, detail, _ in surprises:
            print(f"  [{expectation}] {name}: {verdict} ({detail})")
    return 1 if any(r[2] in ("SILENT", "CRASH") for r in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
