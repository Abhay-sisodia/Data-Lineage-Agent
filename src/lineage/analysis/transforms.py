"""What an expression does to a value. One classifier, imported by every band.

**This module exists because the rule was written twice and drifted** (stress finding
S2-08). `band0` and `defuse` each carried their own `_transform_of` with their own constant
tuples, and S2-05 - `DECODE` is `CASE` written differently, so it is `conditional` - was
applied to band 0 and never reached band 1. A finding recorded as fixed was half-fixed for
two days, and nothing reported it because no stress package happens to reach a `DECODE`
through def-use.

The lesson is not "remember to edit both". It is that a rule with two homes has no home.
D-4's window-function set was added to both copies by hand on the way to finding this, and
D-5's predicate walk was deliberately given one home for the same reason - see
`analysis.predicates`.

ADR-0001 §4 makes the transform class part of the match key, so a wrong class is a MISS on
both sides: one false positive and one false negative for the same edge. That is what makes
this worth centralising rather than tidying.
"""

from __future__ import annotations

from typing import Any

from sqlglot import exp

from lineage.analysis.builtins import ALL_AGGREGATES
from lineage.ir.model import Transform

# Strongest transform on a path wins: an aggregation over a derived expression is an
# aggregation, and calling it identity would be a miss under ADR-0001 §4.
TRANSFORM_RANK = {
    Transform.IDENTITY: 0,
    Transform.DERIVED: 1,
    Transform.CONDITIONAL: 2,
    Transform.AGGREGATED: 3,
}

AGGREGATE_FUNCTIONS = (exp.Sum, exp.Count, exp.Avg, exp.Min, exp.Max, exp.AggFunc)

# Analytic functions that SELECT an existing value rather than computing one over a set.
# Stress finding S2-07, decision D-3: sqlglot makes these subclasses of `exp.AggFunc`, so
# the catch-all above swept them up and reported `aggregated`.
#
# The rule D-3 fixes is that WINDOW-NESS IS NOT AGGREGATION-NESS. `SUM() OVER ()` stays
# `aggregated` because it computes a total over a set; the `OVER` clause is not what makes
# it one. These three compute nothing - the value written appears verbatim in some row of
# the input, and the window only decides WHICH row supplies it. `NTH_VALUE` is `FIRST_VALUE`
# generalised and is classified with them by the same rule.
#
# LAG and LEAD JOINED THIS SET under S2-08, and it is D-3's rule applied rather than a new
# decision: `LAG(total)` reads another ROW of the same column and computes nothing over a
# set, which is the same argument that moved `FIRST_VALUE`. `sq_03`'s key says so in prose -
# "LAG(total) reads another ROW of the same column" - and then labelled it `aggregated`,
# which no key ever argued for on the merits.
#
# **IT COST NOTHING, AND THAT IS WORTH KNOWING RATHER THAN CELEBRATING.** Not one cell moved
# in phase 0 or either stress package, because BOTH `LAG` instances in the entire corpus are
# `LAG(SUM(...))` - the aggregate is inside the window, so `combine` keeps `aggregated`
# whatever `LAG` itself is classified as. The question was unmeasurable here.
#
# It is not unmeasurable in general. A bare-column `LAG(line_amount)` - which is the form
# real reporting SQL actually writes - is `derived` under this rule and was `aggregated`
# before. Pinned by `tests/test_transforms.py`, because no package exercises it.
VALUE_SELECTING_WINDOW_FUNCTIONS = (
    exp.FirstValue,
    exp.LastValue,
    exp.NthValue,
    exp.Lag,
    exp.Lead,
)

# The output value is SELECTED from alternatives by a test, rather than computed from the
# input. Stress finding S2-05: this used to be `(exp.Case, exp.If)` alone, so DECODE -
# which Oracle's own documentation defines as equivalent to CASE - was classified
# `derived`. Four expressions cost eight cells in stress 2.
#
# GREATEST and LEAST belong here because they choose between two SOURCES; NULLIF because
# it replaces the value with NULL on a test; NVL2 because it tests one argument and
# returns one of two others.
CONDITIONAL_EXPRESSIONS = (
    exp.Case,
    exp.If,
    exp.DecodeCase,
    exp.Nullif,
    exp.Greatest,
    exp.Least,
    exp.Nvl2,
)


def is_aggregate(node: Any) -> bool:
    """Does this node compute a value over a SET, rather than pick one out of it?

    The exclusion is checked first: sqlglot derives `FirstValue` and friends from
    `exp.AggFunc`, so the catch-all in `AGGREGATE_FUNCTIONS` matches them too (S2-07).
    """
    if isinstance(node, VALUE_SELECTING_WINDOW_FUNCTIONS):
        return False
    # The two clauses that hang BESIDE an aggregate rather than under it: the aggregate is
    # their `this`, and the columns they carry are its siblings, not its descendants. A path
    # walked upward from such a column meets the clause and never the aggregate, so without
    # this it came out `derived`:
    #   * `percentile_cont(.5) WITHIN GROUP (ORDER BY x)` - x IS the aggregated value
    #     (finding B1-03);
    #   * `agg(x) FILTER (WHERE c)` - c decides which rows enter the aggregate, exactly as
    #     it does in `agg(CASE WHEN c THEN x END)` (decision D-10).
    if isinstance(node, (exp.WithinGroup, exp.Filter)):
        return is_aggregate(node.this)
    # A built-in aggregate SQLGlot has no class for - `jsonb_agg(x)` - parses as Anonymous
    # and would read as a scalar function (see `analysis.builtins`).
    if isinstance(node, exp.Anonymous):
        return (node.name or "").upper() in ALL_AGGREGATES
    return isinstance(node, AGGREGATE_FUNCTIONS)


def is_conditional(node: Any) -> bool:
    """Does this node SELECT the output from alternatives, rather than compute it?

    `COALESCE` is the one that needs a rule rather than a list, and sqlglot gives `NVL`
    the same node, so the two cannot be told apart by type:

    * `COALESCE(a, b)` over two COLUMNS is a genuine choice of source - the value comes
      from `a` or from `b` depending on a test, which is what conditional means.
    * `NVL(x, 0)` has one column and a constant floor. The column's value flows through
      unchanged whenever it exists; the literal is null-safety, not business logic.
      Calling that conditional would tell a reader there is a branch in the rule when the
      only branch is a null guard.

    So: conditional when more than one argument can actually supply a column. Measured
    rather than assumed - classifying every `COALESCE` as conditional costs one phase-0
    label (`sq_06`'s `NVL(parent.depth, 0) + 1`), and this rule leaves it alone.

    **D-19 (finding B4-02) made that rule every conditional form's.** It lived on COALESCE
    alone, so `COALESCE(x, 0)` was derived while `GREATEST(x, 0)`, `NULLIF(x, 0)` and `CASE
    WHEN x IS NULL THEN 0 ELSE x END` - the same guard spelled three other ways - were
    conditional. Now every form is conditional only when the node reads TWO OR MORE
    DISTINCT COLUMNS, across its tests and results together: a choice that involves another
    column. One column against constants - a guard, a clamp, a mapping, a band - is derived.
    Distinct means distinct reference: `a.x` and `b.x` in a self-join are two, `x` written
    twice is one. Columns are counted where the node is written, so `COALESCE(t.net, 0)`
    reads one column even when `t.net` was built from two.

    **This rule never existed in band 1 at all until S2-08.** `defuse` had the pre-S2-05
    two-element tuple and no COALESCE handling, so a `DECODE` or a two-column `COALESCE`
    reached through def-use was reported `derived`.
    """
    if not isinstance(node, (exp.Coalesce, *CONDITIONAL_EXPRESSIONS)):
        return False
    distinct = {
        (column.table.lower(), column.name.lower()) for column in node.find_all(exp.Column)
    }
    return len(distinct) > 1


def transform_of(expression: Any) -> Transform:
    """Classify what the expression does to the value.

    Typed as Any because sqlglot's node accessors return loosely-typed expressions; the
    narrowing happens here rather than being asserted at every call site.
    """
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if isinstance(expression, exp.Column):
        return Transform.IDENTITY
    if any(is_aggregate(node) for node in expression.walk()):
        return Transform.AGGREGATED
    if any(is_conditional(node) for node in expression.walk()):
        return Transform.CONDITIONAL
    return Transform.DERIVED


def transform_of_path(expression: Any, column: Any) -> Transform:
    """What this expression does to THIS column, rather than to its value in general (D-7).

    `transform_of` above answers a question about an EXPRESSION: is there an aggregate
    anywhere in it? That is the right question when every column enters the same way, and
    the wrong one as soon as they do not:

        gross_amount - COALESCE(discount_amt, 0) - COALESCE((SELECT SUM(refund)), 0)

    One operand passes through an aggregate and two do not. `transform_of` reports
    `aggregated` for all three, which tells a reader that `net_amount` is a sum of
    `gross_amount` - and it is not, it is a single row's value with two things subtracted
    from it.

    **D-7: the ladder ranks the hardest construct ON THE PATH FROM THIS SOURCE TO THIS
    TARGET**, which is what ADR-0001 §4 says and what the expression reading quietly
    widened. So the walk goes from the column UPWARDS to the projection root, and only the
    nodes actually between them count.

    The two readings agree everywhere except when one operand of an expression is
    aggregated and another is not, which is why no package before `stress_pg_04` asked. In
    particular they agree whenever the mixed expression is itself wrapped in an aggregate
    one level up - `SUM(gross - discount - refund)` - which is the ordinary shape and the
    reason `stress_pg_05` is unaffected.

    Returns `IDENTITY` only when the column IS the expression: anything between them did
    something, even if only arithmetic.
    """
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if expression is column:
        return Transform.IDENTITY

    saw_aggregate = False
    saw_conditional = False

    # Upwards from the column, stopping at the projection root. `parent` is a plain
    # attribute walk, so the bound is the tree's own depth; the guard is there because a
    # column that is NOT inside `expression` would otherwise walk to the statement root
    # and classify against nodes that have nothing to do with it.
    node = getattr(column, "parent", None)
    inside = False
    computed = False
    for _ in range(200):
        if node is None:
            break
        if is_aggregate(node):
            saw_aggregate = True
        elif is_conditional(node):
            saw_conditional = True
        elif not isinstance(node, _PASS_THROUGH):
            computed = True
        if node is expression:
            inside = True
            break
        node = getattr(node, "parent", None)

    if not inside:
        # Not reachable from this expression - fall back to the expression-wide reading
        # rather than guessing. Preserves the old answer for any caller that passes a
        # column the expression does not contain.
        return transform_of(expression)

    if saw_aggregate:
        return Transform.AGGREGATED
    if saw_conditional:
        return Transform.CONDITIONAL
    return Transform.DERIVED if computed else Transform.IDENTITY


# Nodes that carry a value without changing it: a column read through a scalar subquery, a
# derived table or a set-operation arm arrives exactly as it was (finding B2-01). Counting
# the wrapper as a computation reported `(SELECT c.full_name FROM c WHERE ...)` as derived.
_PASS_THROUGH = (exp.Alias, exp.Subquery, exp.Select, exp.Paren, exp.From, exp.SetOperation)


def combine(first: Transform, second: Transform) -> Transform:
    """The stronger of two transforms on one path (ADR-0001 §4's ladder)."""
    return first if TRANSFORM_RANK[first] >= TRANSFORM_RANK[second] else second
