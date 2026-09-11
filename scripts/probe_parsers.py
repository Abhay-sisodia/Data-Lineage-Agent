"""Ask both parsers what they accept, construct by construct.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe scripts\\probe_parsers.py

WHY THIS EXISTS. Every other instrument in this project measures the analyser against
the corpus, and **the corpus cannot tell you what it does not contain**. Stress finding
S2-06 is the standing proof: ten unlabelled units hid four fixes' worth of defects behind
a clean-looking score. This attacks the same blind spot from the other side - it asks what
Oracle allows that we reject, rather than what our own examples happen to cover.

Two parsers, and a rejection means something different on each side:

* ANTLR owns the PROGRAM - statement boundaries, declarations, control flow. A rejection
  here is structural and often costs a whole file or unit.
* SQLGlot owns the SQL inside those boundaries. A rejection costs one statement, refused
  as PARSE_FAILED and counted.

So SQLGlot is never asked about PL/SQL, and ANTLR is never asked to interpret SQL.

WHAT THIS IS NOT. It runs no analyser. A construct that parses may still yield no edges,
a wrong transform, or a silent loss - `INSERT ALL` parses cleanly and produces nothing.
**Parsing is a floor, not a score.** `scripts/probe_analyser.py` asks the next question.

Findings are recorded in `docs/grammar_limitations.md` as the GL- and SG- series.

ONE RULE, LEARNED THE HARD WAY. The first run of this probe named every trigger `trg` and
reported six trigger forms as rejected - compound, FOLLOWS, INSTEAD OF, WHEN, DISABLE,
AFTER LOGON. That reading was wrong, and wrong in the most expensive direction: it would
have been logged as "the grammar cannot parse compound triggers", a far larger claim than
the truth. `trg` is swallowed by the FILE_EXT lexer rule (GL-002); the constructs were
always fine. **Vary one thing, and treat a rejection as a hypothesis until the minimal
case confirms it** - the parser equivalent of "a finding is not closed until a regression
test fails without the fix".
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None:  # run as a script, not a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import sqlglot

from lineage.parsing.plsql import parse_program

DIALECT = "oracle"

# (id, category, sql-or-None, plsql-or-None)
#   sql   -> handed to SQLGlot, and wrapped in a procedure for ANTLR
#   plsql -> handed to ANTLR alone (top-level DDL, or a procedural construct)
Probe = tuple[str, str, str | None, str | None]
PROBES: list[Probe] = []


def sql(pid: str, category: str, text: str) -> None:
    PROBES.append((pid, category, text, None))


def plsql(pid: str, category: str, text: str) -> None:
    PROBES.append((pid, category, None, text))


def _wrap(statement: str) -> str:
    return f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n{statement};\nEND;\n/\n"


# --------------------------------------------------------------------- DML shapes
sql("merge-delete", "DML", """MERGE INTO tgt t USING src s ON (t.id = s.id)
WHEN MATCHED THEN UPDATE SET t.v = s.v DELETE WHERE s.flag = 'D'
WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v)""")
sql("insert-all", "DML", """INSERT ALL INTO a (c) VALUES (x) INTO b (c) VALUES (y)
SELECT x, y FROM src""")
sql("insert-first", "DML", """INSERT FIRST WHEN x > 1 THEN INTO a (c) VALUES (x)
ELSE INTO b (c) VALUES (x) SELECT x FROM src""")
sql("returning-into", "DML", "UPDATE t SET v = 1 WHERE id = 2 RETURNING v INTO :b")
sql("log-errors", "DML", "INSERT INTO t SELECT * FROM s LOG ERRORS INTO err$t REJECT LIMIT UNLIMITED")
sql("multi-column-in", "DML", "SELECT a FROM t WHERE (a, b) IN (SELECT c, d FROM s)")
sql("update-set-row", "DML", "UPDATE t SET ROW = (SELECT * FROM s WHERE s.id = t.id)")
sql("update-from-subq", "DML", "UPDATE (SELECT a, b FROM t JOIN s ON t.id = s.id) SET a = b")

# --------------------------------------------------------------------- query clauses
sql("connect-by", "hierarchical", """SELECT emp_id, LEVEL, SYS_CONNECT_BY_PATH(name, '/') p
FROM emp START WITH mgr IS NULL CONNECT BY PRIOR emp_id = mgr""")
sql("connect-by-root", "hierarchical", "SELECT CONNECT_BY_ROOT name r, CONNECT_BY_ISLEAF FROM emp CONNECT BY PRIOR id = pid")
sql("connect-by-nocycle", "hierarchical", "SELECT id FROM emp CONNECT BY NOCYCLE PRIOR id = pid")
sql("model", "analytic", """SELECT p, s FROM sales MODEL DIMENSION BY (p) MEASURES (s)
RULES (s['x'] = s['y'] + 1)""")
sql("match-recognize", "analytic", """SELECT * FROM ticker MATCH_RECOGNIZE (
PARTITION BY sym ORDER BY dt MEASURES A.price AS ap PATTERN (A B+)
DEFINE B AS B.price > A.price)""")
sql("pivot", "pivot", "SELECT * FROM t PIVOT (SUM(amt) FOR mth IN ('01' AS jan, '02' AS feb))")
sql("pivot-subquery-in", "pivot", "SELECT * FROM t PIVOT (SUM(amt) FOR mth IN (SELECT m FROM months))")
sql("pivot-xml", "pivot", "SELECT * FROM t PIVOT XML (SUM(amt) FOR mth IN (ANY))")
sql("unpivot", "pivot", "SELECT * FROM t UNPIVOT (amt FOR mth IN (jan AS '01', feb AS '02'))")
sql("grouping-sets", "grouping", "SELECT a, b, SUM(x) FROM t GROUP BY GROUPING SETS ((a), (b), ())")
sql("cube", "grouping", "SELECT a, b, SUM(x) FROM t GROUP BY CUBE (a, b)")
sql("rollup", "grouping", "SELECT a, b, SUM(x) FROM t GROUP BY ROLLUP (a, b)")
sql("grouping-id", "grouping", "SELECT GROUPING_ID(a, b), SUM(x) FROM t GROUP BY CUBE (a, b)")
sql("having-grouping", "grouping", "SELECT a, SUM(x) FROM t GROUP BY a HAVING GROUPING(a) = 0")

# --------------------------------------------------------------------- joins, row sources
sql("oracle-outer-join", "join", "SELECT a.x FROM a, b WHERE a.id = b.id(+)")
sql("lateral", "join", "SELECT * FROM d, LATERAL (SELECT * FROM e WHERE e.did = d.id)")
sql("cross-apply", "join", "SELECT * FROM d CROSS APPLY (SELECT * FROM e WHERE e.did = d.id)")
sql("outer-apply", "join", "SELECT * FROM d OUTER APPLY (SELECT * FROM e WHERE e.did = d.id)")
sql("natural-join", "join", "SELECT * FROM a NATURAL JOIN b")
sql("join-using", "join", "SELECT * FROM a JOIN b USING (id)")
sql("partitioned-outer-join", "join", "SELECT * FROM t PARTITION BY (a) RIGHT OUTER JOIN d ON t.d = d.d")
sql("table-function", "row-source", "SELECT * FROM TABLE(pkg.f(1))")
sql("cast-multiset", "row-source", "SELECT CAST(MULTISET(SELECT id FROM t) AS id_tab) FROM dual")
sql("xmltable", "row-source", """SELECT x.a FROM t, XMLTABLE('/r' PASSING t.doc COLUMNS a VARCHAR2(9) PATH 'a') x""")
sql("json-table", "row-source", """SELECT j.a FROM t, JSON_TABLE(t.doc, '$' COLUMNS (a VARCHAR2(9) PATH '$.a')) j""")
sql("partition-extended", "row-source", "SELECT * FROM sales PARTITION (p_2026)")
sql("subpartition-extended", "row-source", "SELECT * FROM sales SUBPARTITION (sp_1)")
sql("partition-for", "row-source", "SELECT * FROM sales PARTITION FOR (DATE '2026-01-01')")
sql("sample", "row-source", "SELECT * FROM t SAMPLE (10) SEED (1)")
sql("dblink", "row-source", "SELECT c.id FROM customer@crm_link c")
sql("flashback-scn", "row-source", "SELECT * FROM t AS OF SCN 12345")
sql("flashback-ts", "row-source", "SELECT * FROM t AS OF TIMESTAMP SYSTIMESTAMP - 1")
sql("flashback-versions", "row-source", "SELECT versions_starttime, id FROM t VERSIONS BETWEEN SCN MINVALUE AND MAXVALUE")

# --------------------------------------------------------------------- functions, literals
sql("listagg", "function", "SELECT LISTAGG(n, ',') WITHIN GROUP (ORDER BY n) FROM t")
sql("listagg-overflow", "function", "SELECT LISTAGG(n, ',' ON OVERFLOW TRUNCATE) WITHIN GROUP (ORDER BY n) FROM t")
sql("keep-dense-rank", "function", "SELECT MAX(v) KEEP (DENSE_RANK FIRST ORDER BY d) FROM t")
sql("ignore-nulls", "function", "SELECT LAST_VALUE(v IGNORE NULLS) OVER (ORDER BY d) FROM t")
sql("window-frame-range", "function", """SELECT SUM(v) OVER (ORDER BY d
RANGE BETWEEN INTERVAL '7' DAY PRECEDING AND CURRENT ROW) FROM t""")
sql("window-groups", "function", "SELECT SUM(v) OVER (ORDER BY d GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM t")
sql("json-value", "function", "SELECT JSON_VALUE(doc, '$.a' RETURNING VARCHAR2(9)) FROM t")
sql("json-object", "function", "SELECT JSON_OBJECT('a' VALUE x, 'b' VALUE y) FROM t")
sql("json-exists", "function", "SELECT id FROM t WHERE JSON_EXISTS(doc, '$.a')")
sql("xmlagg", "function", "SELECT XMLAGG(XMLELEMENT(\"e\", n) ORDER BY n) FROM t")
sql("decode", "function", "SELECT DECODE(s, 'A', 1, 'B', 2, 0) FROM t")
sql("nvl2", "function", "SELECT NVL2(a, b, c) FROM t")
sql("named-arg", "function", "SELECT pkg.f(p_x => 1, p_y => 2) FROM dual")
sql("nextval", "function", "SELECT s.NEXTVAL FROM dual")
sql("interval-literal", "literal", "SELECT d + INTERVAL '3' MONTH FROM t")
sql("date-literal", "literal", "SELECT * FROM t WHERE d > DATE '2026-01-01'")
sql("timestamp-tz-literal", "literal", "SELECT TIMESTAMP '2026-01-01 00:00:00 -05:00' FROM dual")
sql("q-quote", "literal", "SELECT q'[it's]' FROM dual")
sql("nq-literal", "literal", "SELECT N'abc' FROM dual")

# --------------------------------------------------------------------- query structure
sql("recursive-with", "structure", """WITH r (n) AS (SELECT 1 FROM dual UNION ALL
SELECT n + 1 FROM r WHERE n < 5) SELECT n FROM r""")
sql("with-function", "structure", """WITH FUNCTION f(x NUMBER) RETURN NUMBER IS BEGIN RETURN x; END;
SELECT f(id) FROM t""")
sql("search-cycle", "structure", """WITH r (n) AS (SELECT 1 FROM dual UNION ALL SELECT n+1 FROM r WHERE n<5)
SEARCH DEPTH FIRST BY n SET ord CYCLE n SET cyc TO 'Y' DEFAULT 'N' SELECT n FROM r""")
sql("minus", "structure", "SELECT a FROM t MINUS SELECT a FROM s")
sql("intersect", "structure", "SELECT a FROM t INTERSECT SELECT a FROM s")
sql("fetch-first", "structure", "SELECT a FROM t ORDER BY a FETCH FIRST 10 ROWS ONLY")
sql("offset-fetch-ties", "structure", "SELECT a FROM t ORDER BY a OFFSET 5 ROWS FETCH NEXT 10 ROWS WITH TIES")
sql("hint", "structure", "SELECT /*+ FULL(t) PARALLEL(t, 4) */ a FROM t")
sql("for-update-of", "structure", "SELECT a FROM t FOR UPDATE OF t.a NOWAIT")
sql("for-update-skip", "structure", "SELECT a FROM t FOR UPDATE SKIP LOCKED")

# --------------------------------------------------------------------- PL/SQL statements
plsql("exit-when", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS SELECT id FROM t;
  r c%ROWTYPE;
BEGIN
  OPEN c;
  LOOP
    FETCH c INTO r;
    EXIT WHEN c%NOTFOUND;
  END LOOP;
  CLOSE c;
END;
/
""")
plsql("lock-table", "plsql-stmt", _wrap("LOCK TABLE t IN EXCLUSIVE MODE"))
plsql("where-current-of", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS SELECT id FROM t FOR UPDATE;
BEGIN
  FOR r IN c LOOP
    UPDATE t SET v = 1 WHERE CURRENT OF c;
  END LOOP;
END;
/
""")
plsql("forall-save-exceptions", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER;
  ids t_ids;
BEGIN
  FORALL i IN 1 .. ids.COUNT SAVE EXCEPTIONS
    INSERT INTO t (id) VALUES (ids(i));
END;
/
""")
plsql("forall-indices-of", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
  ids t_ids;
BEGIN
  FORALL i IN INDICES OF ids
    INSERT INTO t (id) VALUES (ids(i));
END;
/
""")
plsql("forall-values-of", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
  ids t_ids; ix t_ids;
BEGIN
  FORALL i IN VALUES OF ix
    INSERT INTO t (id) VALUES (ids(i));
END;
/
""")
plsql("bulk-collect-limit", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS SELECT id FROM t;
  TYPE t_ids IS TABLE OF NUMBER;
  ids t_ids;
BEGIN
  OPEN c;
  FETCH c BULK COLLECT INTO ids LIMIT 100;
  CLOSE c;
END;
/
""")
plsql("returning-bulk-collect", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER;
  ids t_ids;
BEGIN
  UPDATE t SET v = 1 RETURNING id BULK COLLECT INTO ids;
END;
/
""")
plsql("execute-immediate-using-returning", "plsql-stmt", _wrap(
    "EXECUTE IMMEDIATE 'UPDATE t SET v = :1 RETURNING id INTO :2' USING 1 RETURNING INTO v_id"))
plsql("open-for-dynamic", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
  c SYS_REFCURSOR;
BEGIN
  OPEN c FOR 'SELECT id FROM t' USING 1;
END;
/
""")
plsql("goto-label", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  GOTO done;
  <<done>>
  NULL;
END;
/
""")
plsql("continue-when", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  FOR i IN 1 .. 10 LOOP
    CONTINUE WHEN MOD(i, 2) = 0;
  END LOOP;
END;
/
""")
plsql("case-statement", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p (x NUMBER) IS
BEGIN
  CASE
    WHEN x > 1 THEN NULL;
    ELSE NULL;
  END CASE;
END;
/
""")
plsql("nested-block-label", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  <<outer_blk>>
  BEGIN
    NULL;
  END outer_blk;
END;
/
""")
plsql("savepoint", "plsql-stmt", _wrap("SAVEPOINT s1"))
plsql("set-transaction", "plsql-stmt", _wrap("SET TRANSACTION READ ONLY NAME 'r'"))
plsql("raise-application-error", "plsql-stmt", _wrap("RAISE_APPLICATION_ERROR(-20001, 'x')"))
plsql("exception-others", "plsql-stmt", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  NULL;
EXCEPTION
  WHEN NO_DATA_FOUND THEN NULL;
  WHEN OTHERS THEN RAISE;
END;
/
""")

# --------------------------------------------------------------------- declarations
plsql("assoc-array-varchar-index", "plsql-decl", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_map IS TABLE OF NUMBER INDEX BY VARCHAR2(30);
  m t_map;
BEGIN
  NULL;
END;
/
""")
plsql("varray", "plsql-decl", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_v IS VARRAY(10) OF NUMBER;
  v t_v;
BEGIN
  NULL;
END;
/
""")
plsql("record-type", "plsql-decl", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_r IS RECORD (id NUMBER, nm VARCHAR2(9));
  r t_r;
BEGIN
  NULL;
END;
/
""")
plsql("ref-cursor-strong", "plsql-decl", """CREATE OR REPLACE PACKAGE pkg IS
  TYPE t_c IS REF CURSOR RETURN t%ROWTYPE;
END pkg;
/
""")
plsql("subtype-constrained", "plsql-decl", """CREATE OR REPLACE PROCEDURE p IS
  SUBTYPE small IS PLS_INTEGER RANGE 1 .. 10;
  n small;
BEGIN
  NULL;
END;
/
""")
plsql("nocopy-default", "plsql-decl", """CREATE OR REPLACE PROCEDURE p (
  o OUT NOCOPY NUMBER, d IN NUMBER DEFAULT 1, e IN NUMBER := 2) IS
BEGIN
  NULL;
END;
/
""")
plsql("type-rowtype-anchor", "plsql-decl", """CREATE OR REPLACE PROCEDURE p IS
  v t.c%TYPE;
  r t%ROWTYPE;
BEGIN
  NULL;
END;
/
""")

# --------------------------------------------------------------------- unit-level
plsql("pragma-autonomous", "plsql-unit", """CREATE OR REPLACE PROCEDURE p IS
  PRAGMA AUTONOMOUS_TRANSACTION;
BEGIN
  COMMIT;
END;
/
""")
plsql("pragma-exception-init", "plsql-unit", """CREATE OR REPLACE PROCEDURE p IS
  e EXCEPTION;
  PRAGMA EXCEPTION_INIT(e, -20001);
BEGIN
  NULL;
END;
/
""")
plsql("pragma-udf", "plsql-unit", """CREATE OR REPLACE FUNCTION f RETURN NUMBER IS
  PRAGMA UDF;
BEGIN
  RETURN 1;
END;
/
""")
plsql("pragma-inline", "plsql-unit", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  PRAGMA INLINE (f, 'YES');
  NULL;
END;
/
""")
plsql("pragma-serially-reusable", "plsql-unit", """CREATE OR REPLACE PACKAGE pkg IS
  PRAGMA SERIALLY_REUSABLE;
  PROCEDURE p;
END pkg;
/
""")
plsql("pragma-restrict-references", "plsql-unit", """CREATE OR REPLACE PACKAGE pkg IS
  FUNCTION f RETURN NUMBER;
  PRAGMA RESTRICT_REFERENCES(f, WNDS, RNPS);
END pkg;
/
""")
plsql("authid", "plsql-unit", """CREATE OR REPLACE PROCEDURE p AUTHID CURRENT_USER IS
BEGIN
  NULL;
END;
/
""")
plsql("accessible-by", "plsql-unit", """CREATE OR REPLACE PROCEDURE p ACCESSIBLE BY (PACKAGE pkg) IS
BEGIN
  NULL;
END;
/
""")
plsql("result-cache", "plsql-unit", """CREATE OR REPLACE FUNCTION f RETURN NUMBER RESULT_CACHE IS
BEGIN
  RETURN 1;
END;
/
""")
plsql("deterministic-parallel", "plsql-unit", """CREATE OR REPLACE FUNCTION f (x NUMBER) RETURN NUMBER
  DETERMINISTIC PARALLEL_ENABLE IS
BEGIN
  RETURN x;
END;
/
""")
plsql("pipelined", "plsql-unit", """CREATE OR REPLACE FUNCTION f RETURN t_tab PIPELINED IS
BEGIN
  PIPE ROW (1);
  RETURN;
END;
/
""")
plsql("sql-macro", "plsql-unit", """CREATE OR REPLACE FUNCTION f RETURN VARCHAR2 SQL_MACRO IS
BEGIN
  RETURN 'SELECT 1 FROM dual';
END;
/
""")
plsql("conditional-compilation", "plsql-unit", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  $IF DBMS_DB_VERSION.VER_LE_11 $THEN
    NULL;
  $ELSE
    NULL;
  $END
END;
/
""")
plsql("inquiry-directive", "plsql-unit", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  DBMS_OUTPUT.PUT_LINE($$PLSQL_UNIT);
END;
/
""")
plsql("package-init-section", "plsql-unit", """CREATE OR REPLACE PACKAGE BODY pkg IS
  g NUMBER;
  PROCEDURE p IS BEGIN NULL; END p;
BEGIN
  g := 1;
END pkg;
/
""")

# --------------------------------------------------------------------- triggers and types
#
# NOTE THE TRIGGER NAMES. Every one is `t_aud`, never `trg` - see GL-002 and the module
# docstring. Naming them `trg` reports all six as rejected and blames the constructs.
plsql("compound-trigger", "trigger", """CREATE OR REPLACE TRIGGER t_aud FOR UPDATE ON t
COMPOUND TRIGGER
  BEFORE STATEMENT IS BEGIN NULL; END BEFORE STATEMENT;
  AFTER EACH ROW IS BEGIN NULL; END AFTER EACH ROW;
END t_aud;
/
""")
plsql("trigger-follows", "trigger", """CREATE OR REPLACE TRIGGER t_aud AFTER INSERT ON t
FOR EACH ROW FOLLOWS other_trigger
BEGIN
  NULL;
END;
/
""")
plsql("instead-of-trigger", "trigger", """CREATE OR REPLACE TRIGGER t_aud INSTEAD OF INSERT ON v
FOR EACH ROW
BEGIN
  INSERT INTO t (id) VALUES (:NEW.id);
END;
/
""")
plsql("trigger-when-clause", "trigger", """CREATE OR REPLACE TRIGGER t_aud BEFORE UPDATE OF c ON t
FOR EACH ROW WHEN (NEW.c > OLD.c)
BEGIN
  NULL;
END;
/
""")
plsql("trigger-disable", "trigger", """CREATE OR REPLACE TRIGGER t_aud BEFORE INSERT ON t
FOR EACH ROW DISABLE
BEGIN
  NULL;
END;
/
""")
plsql("system-trigger", "trigger", """CREATE OR REPLACE TRIGGER t_aud AFTER LOGON ON SCHEMA
BEGIN
  NULL;
END;
/
""")
plsql("ddl-trigger", "trigger", """CREATE OR REPLACE TRIGGER t_aud AFTER DDL ON SCHEMA
BEGIN
  NULL;
END;
/
""")
plsql("object-type", "type", """CREATE OR REPLACE TYPE ot AS OBJECT (
  id NUMBER,
  MEMBER FUNCTION f RETURN NUMBER,
  MAP MEMBER FUNCTION m RETURN NUMBER
);
/
""")
plsql("type-under", "type", """CREATE OR REPLACE TYPE sub UNDER ot (
  extra NUMBER,
  OVERRIDING MEMBER FUNCTION f RETURN NUMBER
);
/
""")
plsql("type-body", "type", """CREATE OR REPLACE TYPE BODY ot AS
  MEMBER FUNCTION f RETURN NUMBER IS BEGIN RETURN 1; END;
END;
/
""")
plsql("nested-table-type", "type", "CREATE OR REPLACE TYPE id_tab AS TABLE OF NUMBER;\n/\n")


# GL-002. Kept as its own check rather than as probes, because the defect is in the
# LEXER and applies to every position at once - so the useful question is "which
# identifiers stop being identifiers", not "which construct broke".
FILE_EXT_TOKENS = ["fnc", "pkb", "pks", "prc", "trg", "vw"]
CONTROL_NAMES = ["trg_audit", "vw_sales", "prc_load", "v_trg", "trgs", "atrg"]


def check_sqlglot(text: str) -> str | None:
    try:
        sqlglot.parse_one(text, dialect=DIALECT)
    except Exception as exc:
        return f"{type(exc).__name__}: {str(exc).splitlines()[0][:110]}"
    return None


def check_antlr(text: str) -> str | None:
    program = parse_program(text)
    if program.errors:
        return str(program.errors[0])[:120]
    return None


def identifier_positions(name: str) -> dict[str, str]:
    return {
        "variable": f"CREATE OR REPLACE PROCEDURE p IS\n  {name} NUMBER;\nBEGIN\n  {name} := 1;\nEND;\n/\n",
        "column": f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  INSERT INTO t2 (c) SELECT {name} FROM t1;\nEND;\n/\n",
        "table": f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n  INSERT INTO t2 (c) SELECT c FROM {name};\nEND;\n/\n",
        "trigger name": f"CREATE OR REPLACE TRIGGER {name} AFTER INSERT ON t\nBEGIN\n  NULL;\nEND;\n/\n",
        "procedure name": f"CREATE OR REPLACE PROCEDURE {name} IS\nBEGIN\n  NULL;\nEND;\n/\n",
    }


def main() -> int:
    rows = []
    for pid, category, sql_text, plsql_text in PROBES:
        if sql_text is not None:
            glot = check_sqlglot(sql_text)
            antlr = check_antlr(_wrap(sql_text))
        else:
            glot = None
            antlr = check_antlr(plsql_text or "")
        rows.append((pid, category, antlr, glot, sql_text is not None))

    print(f"{'id':34} {'category':14} {'ANTLR':8} {'SQLGlot':8}")
    print("-" * 74)
    for pid, category, antlr, glot, is_sql in rows:
        antlr_cell = "REJECT" if antlr else "ok"
        glot_cell = ("REJECT" if glot else "ok") if is_sql else "-"
        flag = "  <<<" if (antlr or glot) else ""
        print(f"{pid:34} {category:14} {antlr_cell:8} {glot_cell:8}{flag}")

    rejected = [r for r in rows if r[2] or r[3]]
    print(f"\n{len(rows)} constructs probed, {len(rejected)} rejected\n")

    print("=== FAILURE DETAIL ===")
    for pid, _, antlr, glot, _ in rows:
        if antlr:
            print(f"[{pid}] ANTLR   {antlr}")
        if glot:
            print(f"[{pid}] SQLGLOT {glot}")

    print("\n=== GL-002 · FILE_EXT identifiers ===")
    for name in FILE_EXT_TOKENS + CONTROL_NAMES:
        verdicts = [
            f"{label}={'REJECT' if check_antlr(source) else 'ok'}"
            for label, source in identifier_positions(name).items()
        ]
        print(f"  {name:12} " + "  ".join(verdicts))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
