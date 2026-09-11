"""Of the constructs that PARSE, which produce edges, which refuse, and which go silent.

Run from the repository root:

    .\\.venv\\Scripts\\python.exe scripts\\probe_analyser.py

`scripts/probe_parsers.py` asks "does it parse". This asks the question that matters after
that: what does the ANALYSER do with it. Every construct here is one the parser probe
recorded as accepted by both parsers, so a silent result cannot be blamed on parsing.

Five buckets, and only the last two are defects:

  EDGES    at least one edge came out. **NOT a claim the edges are CORRECT** - only a
           ground-truth key proves that, and no key exists for anything in this file.
           This bucket narrows where to spend hand-written keys; it concludes nothing.
  REFUSED  no edge, and the analyser refused. Honest, counted, survivable - a refusal is
           a deliverable.
  DECLARED no edge and no refusal, but a boundary NAMES what stopped: an unreadable row
           source, a db-link target, a statement that would not parse. Also honest.
  SILENT   no edge, no refusal, nothing declared. The failure mode this product exists to
           prevent, and the reason this script exists.
  CRASH    an exception escaped `analyse_source`. Worse than silent: every other unit in
           the file is lost too, and no count records that it happened.

**The DECLARED bucket was added after the first run over-reported.** `TABLE(f(1))` and a
fully remote db-link select were both bucketed SILENT while each was in fact declaring a
`dangling_reference` naming exactly what it could not read. An instrument that cannot tell
a declared boundary from silence is measuring the wrong thing in a project whose entire
thesis is that difference.

`context_dependent_binding` is deliberately NOT counted as explanatory: it appears on
almost every unit as routine annotation, so admitting it would let any construct explain
its own silence.

WHAT A RESULT HERE IS WORTH. A SILENT or CRASH row is a HYPOTHESIS, not a finding. When
this probe was first run, six constructs came back silent and four of them were the probe
rather than the analyser: three trigger cases - whose edges arrive through the DICTIONARY
pass, via the procedure that writes the table, exactly as stress finding S2-10 records -
and a recursive CTE whose source was the literal `1` from `dual`, so there was no column
lineage to find. Only the crash was real. **Re-test each row on its own, varying one
thing, before writing anything down.**

The dictionary here is synthetic and deliberately generous, so that NAME_NOT_RESOLVED
never becomes the answer. This probe is about construct handling, not name resolution.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ is None:  # run as a script, not a module
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lineage.analysis.procedure import analyse_source
from lineage.ir.model import BoundaryKind
from lineage.resolution.dictionary import Dictionary

SCHEMA = "LINEAGE"

COLUMNS = {
    f"{SCHEMA}.SRC": [
        "ID", "AMT", "DT", "SYM", "CAT", "PID", "DOC", "MGR_ID", "EMP_ID", "NAME",
        "FLAG", "V", "N", "PRICE", "QTY", "NET_AMOUNT", "ORDER_COUNT", "JAN", "FEB",
        "MTH", "A", "B", "C", "X", "Y", "REGION", "LINE_AMOUNT",
    ],
    f"{SCHEMA}.TGT": [
        "ID", "AMT", "DT", "SYM", "CAT", "PID", "V", "N", "TXT", "TOTAL", "RNK",
        "PATHCOL", "LVLCOL", "JAN", "FEB", "MTH", "A", "B", "C", "REGION", "GBP", "USD",
    ],
    f"{SCHEMA}.EMP": ["EMP_ID", "MGR_ID", "NAME", "SAL", "DEPT_ID"],
    f"{SCHEMA}.DEPT": ["DEPT_ID", "DNAME"],
    f"{SCHEMA}.STG": ["ID", "AMT", "REGION"],
    f"{SCHEMA}.AUD": ["ID", "AMT", "WHO"],
}

DICTIONARY = Dictionary(
    captured_at="2026-09-11T00:00:00Z",
    default_schema=SCHEMA,
    objects={
        name: {"owner": SCHEMA, "name": name.split(".")[1], "object_type": "TABLE"}
        for name in COLUMNS
    },
    columns=COLUMNS,
    temporary={name: False for name in COLUMNS},
)

Probe = tuple[str, str, str]
PROBES: list[Probe] = []


def band0(pid: str, category: str, statement: str) -> None:
    """One set-based statement in a bare procedure."""
    PROBES.append((pid, category, f"CREATE OR REPLACE PROCEDURE p IS\nBEGIN\n{statement}\nEND;\n/\n"))


def unit(pid: str, category: str, source: str) -> None:
    """A probe that needs its own declarations or unit shape."""
    PROBES.append((pid, category, source))


# --------------------------------------------------------------------- hierarchical
band0("connect-by", "hierarchical", """
  INSERT INTO tgt (id, lvlcol, pathcol)
  SELECT emp_id, LEVEL, SYS_CONNECT_BY_PATH(name, '/')
  FROM emp START WITH mgr_id IS NULL CONNECT BY PRIOR emp_id = mgr_id;""")
band0("connect-by-root", "hierarchical", """
  INSERT INTO tgt (id, txt)
  SELECT emp_id, CONNECT_BY_ROOT name FROM emp CONNECT BY PRIOR emp_id = mgr_id;""")

# --------------------------------------------------------------------- pivot
band0("pivot-static", "pivot", """
  INSERT INTO tgt (mth, gbp, usd)
  SELECT * FROM (SELECT mth, sym, amt FROM src)
  PIVOT (SUM(amt) FOR sym IN ('GBP' AS gbp, 'USD' AS usd));""")
band0("unpivot", "pivot", """
  INSERT INTO tgt (mth, amt)
  SELECT mth, amt FROM (SELECT mth, jan, feb FROM src)
  UNPIVOT (amt FOR mth2 IN (jan, feb));""")

# --------------------------------------------------------------------- grouping
band0("grouping-sets", "grouping", """
  INSERT INTO tgt (cat, total)
  SELECT cat, SUM(amt) FROM src GROUP BY GROUPING SETS ((cat), ());""")
band0("cube", "grouping", """
  INSERT INTO tgt (cat, total)
  SELECT cat, SUM(amt) FROM src GROUP BY CUBE (cat, region);""")
band0("rollup", "grouping", """
  INSERT INTO tgt (cat, total)
  SELECT cat, SUM(amt) FROM src GROUP BY ROLLUP (cat, region);""")
band0("grouping-id", "grouping", """
  INSERT INTO tgt (rnk, total)
  SELECT GROUPING_ID(cat, region), SUM(amt) FROM src GROUP BY CUBE (cat, region);""")
band0("having-grouping", "grouping", """
  INSERT INTO tgt (cat, total)
  SELECT cat, SUM(amt) FROM src GROUP BY cat HAVING GROUPING(cat) = 0;""")

# --------------------------------------------------------------------- joins
band0("oracle-outer-join", "join", """
  INSERT INTO tgt (id, txt)
  SELECT e.emp_id, d.dname FROM emp e, dept d WHERE e.dept_id = d.dept_id(+);""")
band0("lateral", "join", """
  INSERT INTO tgt (id, amt)
  SELECT d.dept_id, x.sal FROM dept d, LATERAL (SELECT sal FROM emp e WHERE e.dept_id = d.dept_id) x;""")
band0("cross-apply", "join", """
  INSERT INTO tgt (id, amt)
  SELECT d.dept_id, x.sal FROM dept d CROSS APPLY (SELECT sal FROM emp e WHERE e.dept_id = d.dept_id) x;""")
band0("outer-apply", "join", """
  INSERT INTO tgt (id, amt)
  SELECT d.dept_id, x.sal FROM dept d OUTER APPLY (SELECT sal FROM emp e WHERE e.dept_id = d.dept_id) x;""")
band0("natural-join", "join", """
  INSERT INTO tgt (id, txt) SELECT emp_id, dname FROM emp NATURAL JOIN dept;""")
band0("join-using", "join", """
  INSERT INTO tgt (id, txt) SELECT emp_id, dname FROM emp JOIN dept USING (dept_id);""")

# --------------------------------------------------------------------- row sources
band0("table-function", "row-source", """
  INSERT INTO tgt (id) SELECT column_value FROM TABLE(pkg.f(1));""")
band0("table-function-joined", "row-source", """
  INSERT INTO tgt (id, amt) SELECT s.id, t.column_value FROM src s, TABLE(pkg.f(s.id)) t;""")
band0("xmltable", "row-source", """
  INSERT INTO tgt (txt)
  SELECT x.a FROM src, XMLTABLE('/r' PASSING src.doc COLUMNS a VARCHAR2(9) PATH 'a') x;""")
band0("json-table", "row-source", """
  INSERT INTO tgt (txt)
  SELECT j.a FROM src, JSON_TABLE(src.doc, '$' COLUMNS (a VARCHAR2(9) PATH '$.a')) j;""")
band0("partition-extended", "row-source", """
  INSERT INTO tgt (id, amt) SELECT id, amt FROM src PARTITION (p_2026);""")
band0("sample", "row-source", """
  INSERT INTO tgt (id, amt) SELECT id, amt FROM src SAMPLE (10) SEED (1);""")
band0("dblink-all-remote", "row-source", """
  INSERT INTO tgt (id, amt) SELECT id, amt FROM src@crm_link;""")
band0("dblink-mixed", "row-source", """
  INSERT INTO tgt (id, amt)
  SELECT s.id, r.amt FROM src s JOIN src@crm_link r ON s.id = r.id;""")

# --------------------------------------------------------------------- functions
band0("listagg", "function", """
  INSERT INTO tgt (cat, txt)
  SELECT cat, LISTAGG(name, ',') WITHIN GROUP (ORDER BY name) FROM src GROUP BY cat;""")
band0("listagg-overflow", "function", """
  INSERT INTO tgt (cat, txt)
  SELECT cat, LISTAGG(name, ',' ON OVERFLOW TRUNCATE) WITHIN GROUP (ORDER BY name)
  FROM src GROUP BY cat;""")
band0("keep-dense-rank", "function", """
  INSERT INTO tgt (cat, amt)
  SELECT cat, MAX(v) KEEP (DENSE_RANK FIRST ORDER BY dt) FROM src GROUP BY cat;""")
band0("ignore-nulls", "function", """
  INSERT INTO tgt (id, amt)
  SELECT id, LAST_VALUE(v IGNORE NULLS) OVER (ORDER BY dt) FROM src;""")
band0("window-range-interval", "function", """
  INSERT INTO tgt (id, total)
  SELECT id, SUM(amt) OVER (ORDER BY dt RANGE BETWEEN INTERVAL '7' DAY PRECEDING AND CURRENT ROW)
  FROM src;""")
band0("lag-bare-column", "function", """
  INSERT INTO tgt (id, amt)
  SELECT id, LAG(line_amount) OVER (PARTITION BY cat ORDER BY dt) FROM src;""")
band0("json-object", "function", """
  INSERT INTO tgt (txt) SELECT JSON_OBJECT('a' VALUE x, 'b' VALUE y) FROM src;""")
band0("json-exists", "function", """
  INSERT INTO tgt (id) SELECT id FROM src WHERE JSON_EXISTS(doc, '$.a');""")
band0("xmlagg", "function", """
  INSERT INTO tgt (cat, txt)
  SELECT cat, XMLAGG(XMLELEMENT("e", name) ORDER BY name) FROM src GROUP BY cat;""")
band0("nvl2", "function", """
  INSERT INTO tgt (id, amt) SELECT id, NVL2(a, b, c) FROM src;""")
band0("named-arg", "function", """
  INSERT INTO tgt (id, amt) SELECT id, pkg.f(p_x => amt) FROM src;""")

# --------------------------------------------------------------------- structure
band0("recursive-with-literal", "structure", """
  INSERT INTO tgt (id)
  WITH r (n) AS (SELECT 1 FROM dual UNION ALL SELECT n + 1 FROM r WHERE n < 5)
  SELECT n FROM r;""")
band0("recursive-with-table", "structure", """
  INSERT INTO tgt (id, amt)
  WITH r (eid, amt) AS (
    SELECT emp_id, amt FROM src WHERE mgr_id IS NULL
    UNION ALL
    SELECT s.emp_id, s.amt FROM src s JOIN r ON s.mgr_id = r.eid)
  SELECT eid, amt FROM r;""")
band0("minus", "structure", """
  INSERT INTO tgt (id, amt) SELECT id, amt FROM src MINUS SELECT id, amt FROM stg;""")
band0("intersect", "structure", """
  INSERT INTO tgt (id, amt) SELECT id, amt FROM src INTERSECT SELECT id, amt FROM stg;""")
band0("fetch-first", "structure", """
  INSERT INTO tgt (id, amt) SELECT id, amt FROM src ORDER BY amt FETCH FIRST 10 ROWS ONLY;""")
band0("offset-fetch-ties", "structure", """
  INSERT INTO tgt (id, amt)
  SELECT id, amt FROM src ORDER BY amt OFFSET 5 ROWS FETCH NEXT 10 ROWS WITH TIES;""")
band0("hint", "structure", """
  INSERT INTO tgt (id, amt) SELECT /*+ FULL(src) PARALLEL(src, 4) */ id, amt FROM src;""")
band0("insert-all", "structure", """
  INSERT ALL INTO tgt (id, amt) VALUES (id, amt) INTO stg (id, amt) VALUES (id, amt)
  SELECT id, amt FROM src;""")
band0("insert-first", "structure", """
  INSERT FIRST WHEN amt > 100 THEN INTO tgt (id, amt) VALUES (id, amt)
  ELSE INTO stg (id, amt) VALUES (id, amt) SELECT id, amt FROM src;""")

# --------------------------------------------------------------------- band 1, def-use
unit("bulk-collect-limit", "band1", """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS SELECT id, amt FROM src;
  TYPE t_rows IS TABLE OF c%ROWTYPE;
  rows_v t_rows;
BEGIN
  OPEN c;
  LOOP
    FETCH c BULK COLLECT INTO rows_v LIMIT 100;
    EXIT WHEN rows_v.COUNT = 0;
    FORALL i IN 1 .. rows_v.COUNT
      INSERT INTO tgt (id, amt) VALUES (rows_v(i).id, rows_v(i).amt);
  END LOOP;
  CLOSE c;
END;
/
""")
unit("forall-save-exceptions", "band1", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER;
  ids t_ids;
BEGIN
  SELECT id BULK COLLECT INTO ids FROM src;
  FORALL i IN 1 .. ids.COUNT SAVE EXCEPTIONS
    INSERT INTO tgt (id) VALUES (ids(i));
END;
/
""")
unit("forall-indices-of", "band1", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER INDEX BY PLS_INTEGER;
  ids t_ids;
BEGIN
  SELECT id BULK COLLECT INTO ids FROM src;
  FORALL i IN INDICES OF ids
    INSERT INTO tgt (id) VALUES (ids(i));
END;
/
""")
unit("returning-bulk-collect", "band1", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_ids IS TABLE OF NUMBER;
  ids t_ids;
BEGIN
  UPDATE src SET amt = 1 RETURNING id BULK COLLECT INTO ids;
  FORALL i IN 1 .. ids.COUNT
    INSERT INTO tgt (id) VALUES (ids(i));
END;
/
""")
unit("returning-into-scalar", "band1", """CREATE OR REPLACE PROCEDURE p IS
  v_id NUMBER;
BEGIN
  UPDATE src SET amt = 1 WHERE id = 2 RETURNING id INTO v_id;
  INSERT INTO tgt (id) VALUES (v_id);
END;
/
""")
unit("cursor-for-loop", "band1", """CREATE OR REPLACE PROCEDURE p IS
BEGIN
  FOR r IN (SELECT id, amt FROM src) LOOP
    INSERT INTO tgt (id, amt) VALUES (r.id, r.amt);
  END LOOP;
END;
/
""")
unit("where-current-of", "band1", """CREATE OR REPLACE PROCEDURE p IS
  CURSOR c IS SELECT id, amt FROM tgt FOR UPDATE;
BEGIN
  FOR r IN c LOOP
    UPDATE tgt SET amt = r.amt * 2 WHERE CURRENT OF c;
  END LOOP;
END;
/
""")
unit("record-type-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_r IS RECORD (id NUMBER, amt NUMBER);
  r t_r;
BEGIN
  SELECT id, amt INTO r.id, r.amt FROM src WHERE ROWNUM = 1;
  INSERT INTO tgt (id, amt) VALUES (r.id, r.amt);
END;
/
""")
unit("assoc-array-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  TYPE t_map IS TABLE OF NUMBER INDEX BY VARCHAR2(30);
  m t_map;
BEGIN
  SELECT amt INTO m('x') FROM src WHERE ROWNUM = 1;
  INSERT INTO tgt (amt) VALUES (m('x'));
END;
/
""")
unit("goto-label-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT amt INTO v FROM src WHERE ROWNUM = 1;
  GOTO writeit;
  <<writeit>>
  INSERT INTO tgt (amt) VALUES (v);
END;
/
""")
unit("continue-when-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  FOR r IN (SELECT id, amt FROM src) LOOP
    CONTINUE WHEN r.amt IS NULL;
    v := r.amt;
    INSERT INTO tgt (amt) VALUES (v);
  END LOOP;
END;
/
""")
unit("case-statement-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
  s NUMBER;
BEGIN
  SELECT amt INTO s FROM src WHERE ROWNUM = 1;
  CASE
    WHEN s > 100 THEN v := s;
    ELSE v := 0;
  END CASE;
  INSERT INTO tgt (amt) VALUES (v);
END;
/
""")
unit("exception-handler-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT amt INTO v FROM src WHERE ROWNUM = 1;
  INSERT INTO tgt (amt) VALUES (v);
EXCEPTION
  WHEN NO_DATA_FOUND THEN
    INSERT INTO aud (amt) VALUES (v);
END;
/
""")
unit("nested-procedure-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  PROCEDURE inner_p (x NUMBER) IS
  BEGIN
    INSERT INTO tgt (amt) VALUES (x);
  END inner_p;
  v NUMBER;
BEGIN
  SELECT amt INTO v FROM src WHERE ROWNUM = 1;
  inner_p(v);
END;
/
""")
unit("conditional-compilation-flow", "band1", """CREATE OR REPLACE PROCEDURE p IS
  v NUMBER;
BEGIN
  SELECT amt INTO v FROM src WHERE ROWNUM = 1;
  $IF DBMS_DB_VERSION.VER_LE_11 $THEN
    INSERT INTO tgt (amt) VALUES (v);
  $ELSE
    INSERT INTO stg (amt) VALUES (v);
  $END
END;
/
""")
unit("package-init-flow", "band1", """CREATE OR REPLACE PACKAGE BODY pkg IS
  g NUMBER;
  PROCEDURE q IS
  BEGIN
    INSERT INTO tgt (amt) VALUES (g);
  END q;
BEGIN
  SELECT amt INTO g FROM src WHERE ROWNUM = 1;
END pkg;
/
""")
unit("pipelined-function", "band1", """CREATE OR REPLACE FUNCTION f RETURN SYS.ODCINUMBERLIST PIPELINED IS
BEGIN
  FOR r IN (SELECT amt FROM src) LOOP
    PIPE ROW (r.amt);
  END LOOP;
  RETURN;
END;
/
""")

# --------------------------------------------------------------------- triggers
#
# A TRIGGER FILE ON ITS OWN IS EXPECTED TO BE SILENT, and that is not a defect. Trigger
# edges arrive through the DICTIONARY pass - via whichever procedure writes the table -
# not from the file that happens to declare the trigger. See stress finding S2-10. These
# rows are kept so the expectation is visible rather than assumed; to see the edges, look
# at a procedure that writes the trigger's table with the trigger in the dictionary.
unit("trigger-file-instead-of", "trigger", """CREATE OR REPLACE TRIGGER t_io INSTEAD OF INSERT ON v_tgt
FOR EACH ROW
BEGIN
  INSERT INTO tgt (id, amt) VALUES (:NEW.id, :NEW.amt);
END;
/
""")
unit("trigger-file-when", "trigger", """CREATE OR REPLACE TRIGGER t_when BEFORE INSERT ON tgt
FOR EACH ROW WHEN (NEW.amt > 0)
BEGIN
  INSERT INTO aud (id, amt) VALUES (:NEW.id, :NEW.amt);
END;
/
""")
unit("trigger-file-compound", "trigger", """CREATE OR REPLACE TRIGGER t_comp FOR INSERT ON tgt
COMPOUND TRIGGER
  AFTER EACH ROW IS
  BEGIN
    INSERT INTO aud (id, amt) VALUES (:NEW.id, :NEW.amt);
  END AFTER EACH ROW;
END t_comp;
/
""")

EXPECTED_SILENT = {
    # A trigger FILE on its own. Its edges arrive through the dictionary pass, via
    # whichever procedure writes the table - see S2-10 and the section comment above.
    "trigger-file-instead-of",
    "trigger-file-when",
    "trigger-file-compound",
}
"""Rows whose silence is understood and is not a defect.

Only three, and deliberately so: this set is where a real finding goes to hide, so nothing
belongs in it that has not been re-tested on its own. `recursive-with-literal` and
`pipelined-function` were here until the DECLARED bucket existed - both turned out to be
declaring a boundary, which is a better answer than "expected to be silent" and is why
they were taken out rather than left to pass quietly.
"""


EXPLANATORY_BOUNDARIES = {
    BoundaryKind.DANGLING_REFERENCE,
    BoundaryKind.UNRESOLVED_IDENTIFIER,
    BoundaryKind.PARSE_FAILURE,
    BoundaryKind.DYNAMIC_SQL,
    BoundaryKind.REFUSAL,
    BoundaryKind.DDL_SEMANTICS,
    BoundaryKind.FUSION_HAZARD,
    BoundaryKind.CROSS_UNIT_STATE,
}
"""Boundary kinds that NAME something unreadable, so their presence explains a missing edge.

`CONTEXT_DEPENDENT_BINDING` is excluded on purpose: it is routine annotation present on
almost every unit, and counting it would let any construct explain its own silence.
"""


def classify(source: str) -> tuple[str, int, int, int, str]:
    try:
        result = analyse_source(source, DICTIONARY)
    except Exception as exc:
        return ("CRASH", 0, 0, 0, f"{type(exc).__name__}")
    edges, refusals = len(result.edges), len(result.refusals)
    declared = [b for b in result.boundaries if b.kind in EXPLANATORY_BOUNDARIES]
    if edges:
        bucket = "EDGES"
    elif refusals:
        bucket = "REFUSED"
    elif declared:
        bucket = "DECLARED"
    else:
        bucket = "SILENT"
    if refusals:
        note = str(result.refusals[0].code)
    elif bucket == "DECLARED":
        note = f"{declared[0].kind.value} {declared[0].subject}"[:44]
    else:
        note = ""
    return (bucket, edges, refusals, len(result.boundaries), note)


def main() -> int:
    rows = [(pid, category, *classify(source)) for pid, category, source in PROBES]

    print(f"{'id':30} {'category':12} {'bucket':8} {'edges':>5} {'ref':>4} {'bnd':>4}  note")
    print("-" * 92)
    for pid, category, bucket, edges, refusals, bounds, note in rows:
        unexpected = bucket in ("SILENT", "CRASH") and pid not in EXPECTED_SILENT
        mark = "  <<<" if unexpected else ("  (expected)" if pid in EXPECTED_SILENT else "")
        print(
            f"{pid:30} {category:12} {bucket:8} {edges:5} {refusals:4} {bounds:4}  {note}{mark}"
        )

    counts: dict[str, int] = {}
    for row in rows:
        counts[row[2]] = counts.get(row[2], 0) + 1
    print(f"\ntotal {len(rows)}   " + "   ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    unexplained = [
        (pid, category, bucket)
        for pid, category, bucket, *_ in rows
        if bucket in ("SILENT", "CRASH") and pid not in EXPECTED_SILENT
    ]
    print("\n=== UNEXPLAINED SILENCE OR CRASH - each one a hypothesis, not a finding ===")
    if not unexplained:
        print("  none")
    for pid, category, bucket in unexplained:
        print(f"  {bucket:8} {category:12} {pid}")

    print(
        "\nREMINDER: EDGES means edges appeared, NOT that they are correct. No ground-truth\n"
        "key exists for anything in this file - that is what the stress packages are for."
    )
    return 1 if unexplained else 0


if __name__ == "__main__":
    raise SystemExit(main())
