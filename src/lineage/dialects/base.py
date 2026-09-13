"""What the analyser needs to know about a source dialect.

Phase 0 is Oracle only, and every dialect-specific fact was therefore written where it was
needed: `DIALECT = "oracle"` in five modules, `.upper()` at a hundred and forty-seven sites,
fourteen ANTLR context class names imported directly from a PL/SQL grammar, and six queries
against `all_tab_columns` and its siblings. None of that was wrong — a constant is the right
way to write a fact you have measured exactly once — but it means the cost of a second
dialect is spread across the whole tree instead of sitting in one file.

**This is the seam, and it is deliberately narrow.** It holds only what a survey showed
actually differs between Oracle, PostgreSQL and MySQL. Everything else stays where it is,
because the thing most likely to go wrong in a port like this is inventing abstraction for
differences that turn out not to exist.

WHAT DOES NOT BELONG HERE, measured rather than assumed:

* **The whole of band 0.** `analysis/band0.py` has 71 SQLGlot references and zero references
  to the Oracle grammar. Traversal, scopes, joins, CTEs, windows, the transform ladder and
  filter phase are all expressed against SQLGlot's typed tree, which SQLGlot normalises per
  dialect. Band 0 needs the dialect's NAME and nothing else.
* **`analysis/transforms.py`.** It matches typed nodes - `exp.Sum`, `exp.Case`,
  `exp.DecodeCase`, `exp.Nvl2` - not function-name strings. `NVL` arrives as `Coalesce`
  whichever dialect wrote it, so the transform ladder ports unchanged.
* **The IR, the match key, bands, flows, phases, boundaries, the refusal taxonomy and the
  scoring harness.** None of them mentions a dialect, and none of them should start.

So the seam is: a name for SQLGlot, a rule for folding identifiers, a procedural front end,
and a catalogue. Four things.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Dialect(Protocol):
    """One source dialect, as much of it as the analyser has to distinguish.

    A Protocol rather than a base class: the Oracle implementation predates the seam and is
    assembled out of modules that already exist, so inheritance would mean moving code for
    the sake of a type. Structural typing lets the seam be introduced without touching the
    behaviour underneath it, which is the whole point of doing this step first.
    """

    @property
    def name(self) -> str:
        """The dialect's own name, which is also its SQLGlot dialect name.

        These coincide today - `oracle`, `postgres`, `mysql` are all SQLGlot dialects - and
        the property is named for the dialect rather than for SQLGlot so that a future
        dialect SQLGlot spells differently has somewhere to diverge.
        """
        ...

    def fold(self, identifier: str) -> str:
        """An unquoted identifier as the catalogue stores it.

        **This is the difference most likely to be underestimated.** Oracle folds unquoted
        identifiers to upper case; PostgreSQL folds them to LOWER; MySQL preserves them and
        is case-sensitive for table names on Linux but not on macOS or Windows.

        Every node name, dictionary key, match key and ground-truth label in this project is
        upper-cased. Point that at PostgreSQL unchanged and nothing resolves at all - not a
        subtle degradation, a total failure to bind any name to any relation.

        Only IDENTIFIERS go through here. Upper-casing a keyword, a flow name, a transform
        or a refusal code is not a dialect question and must not be routed through a dialect.
        """
        ...
