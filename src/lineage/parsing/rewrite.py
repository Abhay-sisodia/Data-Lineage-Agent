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


def strip_unparseable_clauses(text: str) -> str:
    """Remove clauses that block the parser and carry no lineage. Idempotent."""
    return _CURRENT_OF.sub("", text)
