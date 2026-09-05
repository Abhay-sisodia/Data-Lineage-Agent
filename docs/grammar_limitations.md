# Grammar limitations

Constructs the PL/SQL grammar cannot parse, found while building the corpus.

These are **loud failures** — the parser refuses rather than binding the wrong object,
which is the survivable kind. Each one becomes a declared boundary and a counted entry
in the coverage statement, never a silent hole. Silent failures are tracked separately,
as corpus cases under `corpus/adversarial/silent/`.

Every entry here is a candidate grammar fix. The cost of fixing is weighed against how
often the construct appears in real code — which is a question the corpus answers, not
one to guess at.

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
