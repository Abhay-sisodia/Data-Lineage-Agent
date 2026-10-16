"""Narrow text rewrites applied before SQLGlot sees a statement.

**This module is a liability and is meant to stay small.** Rewriting SQL text before
parsing it means the tree the analyser reasons about is not quite the statement the
database runs, and every entry here has to earn that by removing something that provably
carries no lineage. The test for admission is not "SQLGlot chokes on it" — plenty of
constructs do, and refusing them is the honest answer. It is:

1. SQLGlot cannot parse the statement **at all**, so the whole thing is lost rather than
   partially read; and
2. the clause being removed names **no columns and no variables**, so removing it cannot
   change any edge — only whether the rest of the statement is readable.

Anything failing (2) belongs in the refusal register instead. A rewrite that drops a real
predicate would silently narrow lineage, which is worse than the parse failure it fixes.
"""

from __future__ import annotations

import re

# `UPDATE t SET c = v WHERE CURRENT OF c_lock` and the DELETE equivalent (stress finding
# S1-04 / decision D-1). SQLGlot fails outright: "Invalid expression / Unexpected token".
#
# The clause is a rowid the open cursor is holding. It names no column and no variable -
# there is nothing in it that could be an edge endpoint - so stripping it loses nothing
# and makes the SET clause readable, which is where the lineage actually is. The rows were
# already chosen by the cursor's OWN `WHERE`, and that predicate is analysed where it is
# written, on the cursor declaration.
#
# The stress-2 key states this answer for `s2_locking` - one value edge from the SET
# clause, no filter edge from the predicate - and it was written before this code existed.
_CURRENT_OF = re.compile(r"\s+WHERE\s+CURRENT\s+OF\s+[A-Za-z_][A-Za-z0-9_$#]*", re.IGNORECASE)


# `INSERT ... SELECT ... FROM a CROSS JOIN tmp_run AS r ON CONFLICT (k) DO UPDATE ...`
# (production pilot, 2026-10-12: four upserts, among them the order fact's). A CROSS JOIN
# takes no ON, so PostgreSQL reads `ON CONFLICT` as the INSERT's clause; SQLGlot reads `ON`
# as the join's condition and fails outright, losing the whole statement.
#
# The opposite of the rule's usual shape - this ADDS a clause rather than removing one -
# but it passes the same test: ` WHERE TRUE` names no column and no variable, so it can
# change no edge. It only ends the FROM clause where PostgreSQL already knows it ends, on
# the same line, so line numbers hold. Only a plain relation (an identifier, optionally
# aliased) directly before `ON CONFLICT` qualifies - that is the end of an INSERT's SELECT.
_CROSS_JOIN_BEFORE_CONFLICT = re.compile(
    r"(\bCROSS\s+JOIN\s+(?:ONLY\s+)?[A-Za-z_\"][\w.$\"]*"
    r"(?:\s+(?:AS\s+)?(?!ON\b)[A-Za-z_\"][\w$\"]*)?)(\s+ON\s+CONFLICT\b)",
    re.IGNORECASE,
)


def strip_unparseable_clauses(text: str) -> str:
    """Rewrite clauses that block the parser and carry no lineage. Idempotent."""
    text = _CURRENT_OF.sub("", text)
    return _CROSS_JOIN_BEFORE_CONFLICT.sub(r"\1 WHERE TRUE\2", text)
