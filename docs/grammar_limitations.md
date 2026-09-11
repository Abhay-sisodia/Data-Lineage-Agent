# Parser limitations

Constructs that Oracle accepts and **our parsers reject**.

These are **loud failures** — the parser refuses rather than binding the wrong object,
which is the survivable kind. Each one becomes a declared boundary and a counted entry
in the coverage statement, never a silent hole. Silent failures are tracked separately,
as corpus cases under `corpus/adversarial/silent/`.

Every entry here is a candidate fix. The cost of fixing is weighed against how often the
construct appears in real code — which is a question the corpus answers, not one to
guess at.

## Two parsers, two series

The analyser runs two parsers with a deliberately sharp boundary (`parsing/plsql.py`),
and a rejection means something different on each side. They are numbered separately so
that boundary does not blur in the register:

| Series | Parser | Owns | A rejection costs |
|---|---|---|---|
| **GL-** | ANTLR, `grammars-v4` PL/SQL grammar (runtime 4.13.2) | the *program* — statement boundaries, declarations, control flow | often the **whole file or unit**, because the error is structural |
| **SG-** | SQLGlot, `dialect="oracle"` (30.18.0) | the *SQL* inside those boundaries | **one statement**, refused as `PARSE_FAILED` |

**Entries GL-002 to GL-004 and SG-001 to SG-014 came from a systematic probe on
2026-09-11**, not from the corpus: 121 documented Oracle constructs handed to both
parsers, each recorded as accept or reject. That is why they carry no corpus case — the
corpus contains none of them, which is itself the point. **The corpus cannot tell you
what it does not contain**, so coverage measured against it alone will always read
better than an estate will.

**Reproduce it:**

```
.\.venv\Scripts\python.exe scripts\probe_parsers.py     # this document's findings
.\.venv\Scripts\python.exe scripts\probe_analyser.py    # what the ANALYSER then does
```

The second script is the necessary companion, because **nothing in this document is a
coverage claim.** Parsing is a floor. `INSERT ALL` parses cleanly and yields no edge;
`TABLE(f(...))` parsed cleanly and crashed the analyser outright (stress finding P-01).
A construct's presence on the accept-list below says only that it reached the analyser.

---

## GL-001 · ANSI date literal in a partition bound

**Found:** 2026-09-05, building the corpus manifest (T0.2)
**Severity:** low — cosmetic in our corpus, unknown frequency in real code
**Status:** open, worked around in the corpus

```sql
-- Rejected by the grammar, accepted by Oracle:
PARTITION p_2026 VALUES LESS THAN (DATE '2027-01-01')

-- Accepted by both:
PARTITION p_2026 VALUES LESS THAN (TO_DATE('2027-01-01', 'YYYY-MM-DD'))
```

The parser reports `extraneous input 'DATE' expecting {... 'TO_DATE', UNSIGNED_INTEGER,
CHAR_STRING ...}`. The partition-bound rule accepts function calls and literals but not
the ANSI `DATE 'yyyy-mm-dd'` form, even though the grammar handles that form elsewhere.

**Impact.** A `CREATE TABLE ... PARTITION BY RANGE` statement using ANSI date literals
fails to parse. The DDL itself carries no column-level lineage, so nothing is lost
directly — but if the failure occurs in a file that *also* contains procedures, the
whole file may be affected, so it is worth fixing before analysing an estate with
heavily partitioned DDL.

**Workaround.** `corpus/adversarial/silent/s4_partition_exchange.sql` uses `TO_DATE` so
that the file tests the construct it exists for.

**Note on what this is not.** Partition exchange itself is a *silent* failure and a
different problem entirely: `ALTER TABLE ... EXCHANGE PARTITION` moves an entire dataset
with no `INSERT` anywhere, so DML-only analysis reports the target has no writer. That
is tracked as silent case s4 and is unaffected by this grammar gap.

---

## GL-002 · `FILE_EXT` swallows six ordinary identifiers

**Found:** 2026-09-11, probing the parsers construct by construct
**Severity:** HIGH — the only entry here that a real estate is likely to hit today
**Status:** open

```antlr
FILE_EXT : 'FNC' | 'PKB' | 'PKS' | 'PRC' | 'TRG' | 'VW';
```

`grammars/plsql/PlSqlLexer.g4:1756`. The rule exists for SQL\*Plus file extensions, but
the lexer applies it everywhere, so **`FNC`, `PKB`, `PKS`, `PRC`, `TRG` and `VW` cease
to be identifiers at all**:

```sql
-- Every one of these is rejected. Every one is legal Oracle:
v_prc   NUMBER;                          -- a variable named for "price"
SELECT vw FROM t;                        -- a column
SELECT c FROM trg;                       -- a table
CREATE OR REPLACE TRIGGER trg ...        -- a trigger name
CREATE OR REPLACE PROCEDURE prc ...      -- a procedure name

-- Accepted, because the rule matches the WHOLE token only:
trg_audit, vw_sales, prc_load, v_trg, trgs, atrg

-- Accepted, because quoting bypasses the lexer rule:
SELECT "TRG" FROM t;
```

**Why this one matters more than the rest of the file.** These are precisely the
abbreviations Oracle shops name objects with, and the failure is a *lexer* error, so it
does not stay local to one statement — it can take a whole package down. Nothing else in
this document is as likely to be hit by code someone has already written.

**How it was found, because the method is the point.** The first probe run named every
trigger `trg` and reported six trigger forms as rejected — compound triggers, `FOLLOWS`,
`INSTEAD OF`, `WHEN`, `DISABLE`, `AFTER LOGON`. That reading was wrong and would have
gone into this file as "the grammar cannot parse compound triggers". Renaming to `t_aud`
parses **all seven trigger forms clean**. The construct was never the problem; the name
was. A probe that varies one thing at a time is the only reason the right finding came
out of it.

**Fix.** Delete the rule, or gate it behind the SQL\*Plus context it belongs to. Nothing
in this analyser reads SQL\*Plus file extensions, so the local fix is a deletion and a
regression test per identifier.

**It is deliberately NOT in `stress/FINDINGS.md`** — decided 2026-09-11, and recorded
there under this ID rather than given an `S` number. That register is defects in the
analyser and its keys; this is a defect in a vendored third-party grammar, where the
analyser's behaviour given that grammar is correct and the fix is a grammar patch.
Duplicating it would break the register's own countability: "every open finding recurred
across stress packages" cannot be checked once the register holds items no stress package
could produce.

**If a production package fails to lex, check this first.** It is the highest-impact entry
in this document, and the failure gives no hint of its cause.

---

## GL-003 · `ACCESSIBLE BY` kills the whole unit

**Found:** 2026-09-11, probing the parsers construct by construct
**Severity:** medium — unit-level, so the blast radius is a whole procedure
**Status:** open

```sql
-- Rejected by the grammar, accepted by Oracle since 12c:
CREATE OR REPLACE PROCEDURE p ACCESSIBLE BY (PACKAGE pkg) IS
BEGIN
  NULL;
END;
```

```
1:30 mismatched input 'ACCESSIBLE'
     expecting {'AS', 'AUTHID', 'DETERMINISTIC', 'IS', 'PARALLEL_ENABLE', '('}
```

`ACCESSIBLE` appears in the very keyword list the error prints as acceptable — the token
exists, the clause is simply missing from the unit header rule.

**Impact is worse than a statement-level gap.** This is a clause on the *unit*, so the
procedure never parses and every statement inside it is lost at once. `AUTHID`,
`DETERMINISTIC`, `PARALLEL_ENABLE`, `RESULT_CACHE`, `PIPELINED` and `SQL_MACRO` all
parse clean; this is the one header clause that does not.

---

## GL-004 · `MATCH_RECOGNIZE` and the `GROUPS` window frame

**Found:** 2026-09-11, probing the parsers construct by construct
**Severity:** low — neither appears in the corpus, and both are uncommon in ETL code
**Status:** open

```sql
-- MATCH_RECOGNIZE: 3:37 missing {<EOF>, ';'} at '('
SELECT * FROM ticker MATCH_RECOGNIZE (
  PARTITION BY sym ORDER BY dt MEASURES A.price AS ap
  PATTERN (A B+) DEFINE B AS B.price > A.price);

-- GROUPS frame: 3:31 mismatched input 'GROUPS' expecting {'RANGE', 'ROWS', ')'}
SELECT SUM(v) OVER (ORDER BY d GROUPS BETWEEN 1 PRECEDING AND CURRENT ROW) FROM t;
```

Note the asymmetry, which is the useful part: **SQLGlot parses both of these happily.**
The rejection is entirely ANTLR's, so the statement is lost at the boundary stage and
SQLGlot never gets to see the SQL it could have handled.

`MATCH_RECOGNIZE` would also need lineage semantics even once it parsed — the `MEASURES`
clause defines columns against pattern variables — so parsing it is necessary but not
sufficient. `RANGE` and `ROWS` frames, including `RANGE BETWEEN INTERVAL '7' DAY
PRECEDING`, parse clean.

---

# SQLGlot limitations

Statement-level. Each of these becomes a `PARSE_FAILED` refusal for the one statement and
is counted against parse coverage — loud, local and survivable. All were probed against
SQLGlot 30.18.0 with `dialect="oracle"`.

## SG-001 · `MERGE … DELETE` — already tracked as stress finding S2-01

```sql
MERGE INTO tgt t USING src s ON (t.id = s.id)
WHEN MATCHED THEN UPDATE SET t.v = s.v DELETE WHERE s.flag = 'D'
WHEN NOT MATCHED THEN INSERT (id, v) VALUES (s.id, s.v);
```

`ParseError: Invalid expression / Unexpected token. Line 2, Col: 45` — the `DELETE`
keyword inside the `WHEN MATCHED` arm. **7 edges, and it is a standard slowly-changing-
dimension shape.** See `stress/FINDINGS.md` S2-01 for the cost analysis.

## SG-002 · Writing statements — the two that lose real edges

| Construct | Example |
|---|---|
| `LOG ERRORS INTO` | `INSERT INTO t SELECT … LOG ERRORS INTO err$t REJECT LIMIT UNLIMITED` |
| Updatable join view | `UPDATE (SELECT a, b FROM t JOIN s ON t.id = s.id) SET a = b` |

**These are grouped with SG-001 deliberately: all three WRITE.** A rejection here loses
lineage that exists, as opposed to losing a read path that some other statement may also
provide. The updatable join view is the more interesting of the two, because the target
is a subquery rather than a table — resolving which base table each column writes to is
real work even after it parses.

## SG-003 · Row-version and partition clauses — cheap, because they change no lineage

| Construct | Example |
|---|---|
| `AS OF SCN` | `FROM t AS OF SCN 12345` |
| `AS OF TIMESTAMP` | `FROM t AS OF TIMESTAMP SYSTIMESTAMP - 1` |
| `VERSIONS BETWEEN` | `FROM t VERSIONS BETWEEN SCN MINVALUE AND MAXVALUE` |
| `PARTITION FOR` | `FROM sales PARTITION FOR (DATE '2026-01-01')` |

**The cheapest group in this file, and the reason is a lineage argument rather than a
parsing one.** Every clause here selects *which version or subset of the rows* is read.
None of them changes which column flows to which column. So a pre-parse rewrite that
strips the clause loses nothing that the IR records — the same technique and the same
justification as `WHERE CURRENT OF` in `lineage/parsing/rewrite.py`, which already
documents the two-part admission test such a rewrite has to pass.

Plain `PARTITION (p_2026)` and `SUBPARTITION (sp_1)` already parse; only the `FOR (…)`
form fails.

## SG-004 · `q'[…]'` alternative quoting — a tokenizer failure

```sql
SELECT q'[it's]' FROM dual;
```

`TokenError: Error tokenizing` — this one fails before parsing begins, so it is not
recoverable by any expression-level handling.

**Likely the highest-frequency entry in the SG series.** Oracle developers reach for
`q'[…]'` whenever a literal contains an apostrophe, which in practice means most
hand-written message and dynamic-SQL code. It is worth measuring against the production
package before anything further down this list.

## SG-005 · Query constructs with no lineage semantics yet anyway

| Construct | Example |
|---|---|
| `MODEL` | `MODEL DIMENSION BY (p) MEASURES (s) RULES (s['x'] = s['y'] + 1)` |
| `PIVOT XML` | `PIVOT XML (SUM(amt) FOR mth IN (ANY))` |
| Partitioned outer join | `FROM t PARTITION BY (a) RIGHT OUTER JOIN d ON t.d = d.d` |
| `JSON_VALUE … RETURNING` | `JSON_VALUE(doc, '$.a' RETURNING VARCHAR2(9))` |
| `WITH FUNCTION` | `WITH FUNCTION f(x NUMBER) RETURN NUMBER IS … SELECT f(id) FROM t` |
| `SEARCH` / `CYCLE` | `SEARCH DEPTH FIRST BY n SET ord CYCLE n SET cyc TO 'Y'` |

`MODEL` and `PIVOT XML` already have refusal codes waiting for them
(`UNSUPPORTED_CONSTRUCT` and `SHAPE_NOT_DECIDABLE` respectively), so **parsing them would
convert a `PARSE_FAILED` into a more honest refusal without yielding a single edge.**
That is worth doing for the taxonomy, not for coverage.

`JSON_VALUE` and `SEARCH`/`CYCLE` are the two that would actually produce lineage:
`JSON_VALUE` without `RETURNING` already parses, so only the clause is missing, and
`SEARCH … SET ord` adds a genuine column to a recursive CTE's projection.

---

# What passes — recorded, because absence of a finding is also a measurement

The same 2026-09-11 probe accepted the following, several of which had been assumed
broken. Listing them stops the same ground being re-probed:

**SQL.** `CONNECT BY` with `PRIOR`, `NOCYCLE`, `SYS_CONNECT_BY_PATH`, `CONNECT_BY_ROOT`,
`CONNECT_BY_ISLEAF`; `PIVOT` and `UNPIVOT` including a subquery `IN`-list; `GROUPING
SETS`, `CUBE`, `ROLLUP`, `GROUPING_ID`; `(+)` outer joins, `LATERAL`, `CROSS APPLY`,
`OUTER APPLY`, `NATURAL JOIN`, `JOIN … USING`; `XMLTABLE`, `JSON_TABLE`, `TABLE(…)`,
`CAST(MULTISET(…))`; `LISTAGG … ON OVERFLOW TRUNCATE`, `KEEP (DENSE_RANK FIRST …)`,
`IGNORE NULLS`, `RANGE BETWEEN INTERVAL '7' DAY PRECEDING`; `SAMPLE … SEED`, database
links, hints, `FOR UPDATE SKIP LOCKED`, `OFFSET … FETCH NEXT … WITH TIES`; recursive
`WITH`, `MINUS`, `INTERSECT`; `INTERVAL`, `DATE`, `TIMESTAMP … -05:00` and `N'…'`
literals; named arguments (`p_x => 1`).

**PL/SQL.** All seven trigger forms — compound, `FOLLOWS`, `INSTEAD OF`, `WHEN`,
`DISABLE`, `AFTER LOGON`, `AFTER DDL`; every `FORALL` variant including `SAVE
EXCEPTIONS`, `INDICES OF` and `VALUES OF`; `BULK COLLECT … LIMIT` and `RETURNING … BULK
COLLECT INTO`; `EXECUTE IMMEDIATE … USING … RETURNING INTO`, `OPEN … FOR` dynamic;
`GOTO` and labels, `CONTINUE WHEN`, `CASE` statements, `WHERE CURRENT OF`, `LOCK TABLE`,
`EXIT WHEN`; associative arrays keyed by `VARCHAR2`, `VARRAY`, `RECORD`, strong `REF
CURSOR`, constrained `SUBTYPE`, `NOCOPY`, `%TYPE`/`%ROWTYPE`; all seven pragmas
(`AUTONOMOUS_TRANSACTION`, `EXCEPTION_INIT`, `UDF`, `INLINE`, `SERIALLY_REUSABLE`,
`RESTRICT_REFERENCES`); `AUTHID`, `RESULT_CACHE`, `DETERMINISTIC PARALLEL_ENABLE`,
`PIPELINED` with `PIPE ROW`, `SQL_MACRO`; conditional compilation and `$$PLSQL_UNIT`;
package initialisation sections; object types with `UNDER`, `OVERRIDING`, `MAP MEMBER`
and `TYPE BODY`.

**`INSERT ALL` and `INSERT FIRST` both parse cleanly**, which corrects an assumption
worth stating plainly: stress finding **S2-02 is not a parse failure**. SQLGlot builds
the tree; the analyser has no semantics for a multi-table insert. That makes it
capability work on a tree that already exists, and a smaller job than "unsupported"
suggests.

`SIZE` and `OF` are rejected as identifiers, but both are genuinely reserved in Oracle,
so that is correct behaviour and not a finding.
