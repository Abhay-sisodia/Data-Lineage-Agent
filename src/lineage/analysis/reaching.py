"""Reaching definitions over the CFG (T2.6).

**What fixed-point iteration is not needed for here, and why that is worth stating.**
Because variables are first-class IR nodes, each statement contributes its own edges and
the chain `gross_amount -> v_total -> lifetime_value` is already complete after a single
pass. Iterating would not add an edge. An implementation that iterated anyway and
reported "converged" would be theatre.

**What it is needed for.** Which definitions reach a use, and in particular whether *any*
does. The spike document's worked example is the case:

    IF p_region = 'EU' THEN
      v_sql := 'UPDATE dim_customer ...';
    END IF;
    EXECUTE IMMEDIATE v_sql;      -- NULL on every other path

An edge claiming a source for `v_sql` is conditional on a path that may never run. Worse
is the partial case: a definition reaches on *some* paths only, so the lineage is real
but not unconditional, and reporting it flat overstates what is known.

Loops are what make this a fixed-point problem rather than a walk: a definition inside a
loop body reaches uses *earlier* in that body via the back edge, so the analysis has to
iterate until the reaching sets stop changing.

The iteration cap comes from configuration and is reported when hit. A limit that
silently truncates is a hidden defect; the same limit, declared, is a specification.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lineage.analysis.cfg import Cfg

# variable name -> the CFG nodes that may have defined it
ReachingSet = dict[str, frozenset[int]]


@dataclass
class ReachingResult:
    """Definitions reaching the entry of each CFG node."""

    incoming: dict[int, ReachingSet] = field(default_factory=dict)
    iterations: int = 0
    converged: bool = True

    def reaching(self, node_id: int, variable: str) -> frozenset[int]:
        return self.incoming.get(node_id, {}).get(variable.upper(), frozenset())


@dataclass(frozen=True)
class UninitialisedUse:
    """A variable read where no definition, or only some definitions, reach it."""

    variable: str
    node_id: int
    line: int
    partial: bool
    external: bool = False

    def describe(self) -> str:
        if self.external:
            # Package state written by a different procedure. The lineage is real; what
            # is conditional is CALL ORDER, and that is a finding in its own right -
            # calling this procedure without its initialiser leaves the value NULL and
            # the filter matching nothing.
            return (
                f"{self.variable} is read at line {self.line} but is assigned in another "
                f"program unit - the edge is real, and conditional on call order within "
                f"the session rather than on any branch in this unit"
            )
        if self.partial:
            return (
                f"{self.variable} is read at line {self.line} but is only assigned on "
                f"some paths - any edge sourced from it is conditional on a path that "
                f"may not have run"
            )
        return (
            f"{self.variable} is read at line {self.line} with no assignment reaching it "
            f"on any path - its value is NULL and any claimed source is wrong"
        )


def reaching_definitions(
    cfg: Cfg,
    definitions: dict[int, set[str]],
    iteration_cap: int,
) -> ReachingResult:
    """Standard forward dataflow: IN[n] is the union of OUT[p]; OUT[n] is gen[n] plus
    IN[n] minus kill[n].

    Exception edges are included. An exception can be raised part-way through a
    procedure, so a handler sees whatever definitions had executed by then - which is
    precisely why an error-path write can source a value the happy path also produced.
    """
    incoming: dict[int, ReachingSet] = {node_id: {} for node_id in cfg.nodes}
    outgoing: dict[int, ReachingSet] = {node_id: {} for node_id in cfg.nodes}

    order = sorted(cfg.nodes)
    result = ReachingResult(incoming=incoming)

    for iteration in range(1, iteration_cap + 1):
        changed = False

        for node_id in order:
            merged: ReachingSet = {}
            for edge in cfg.predecessors(node_id):
                # Back edges are exactly what makes this iterative: a definition later
                # in a loop body reaches uses earlier in it on the next pass.
                for variable, sources in outgoing[edge.source].items():
                    merged[variable] = merged.get(variable, frozenset()) | sources

            if merged != incoming[node_id]:
                incoming[node_id] = merged
                changed = True

            defined = definitions.get(node_id, set())
            after = dict(merged)
            for variable in defined:
                after[variable] = frozenset({node_id})  # a definition kills prior ones

            if after != outgoing[node_id]:
                outgoing[node_id] = after
                changed = True

        result.iterations = iteration
        if not changed:
            result.converged = True
            return result

    result.converged = False
    return result


def uninitialised_uses(
    cfg: Cfg,
    definitions: dict[int, set[str]],
    uses: dict[int, set[str]],
    result: ReachingResult,
    *,
    parameters: set[str] | None = None,
    external: set[str] | None = None,
    iteration_cap: int = 10,
) -> list[UninitialisedUse]:
    """Variables read where no definition, or only some definitions, reach them.

    Parameters are excluded: they are defined by the caller, which is outside this CFG.
    Package state is reported separately - it is assigned in another unit, so the edge is
    real and what is conditional is call order.
    """
    known = {p.upper() for p in (parameters or set())}
    package_state = {p.upper() for p in (external or set())}
    certain = definitely_assigned(cfg, definitions, iteration_cap)
    found: list[UninitialisedUse] = []

    for node_id, used in uses.items():
        node = cfg.nodes.get(node_id)
        if node is None:
            continue
        for variable in sorted(used):
            if variable in known:
                continue
            reaching = result.reaching(node_id, variable)
            if reaching:
                # Reached by at least one definition. Whether it reaches on EVERY path
                # is a stronger question, answered below.
                if variable not in certain.get(node_id, frozenset()):
                    found.append(UninitialisedUse(variable, node_id, node.line, partial=True))
                continue
            found.append(
                UninitialisedUse(
                    variable,
                    node_id,
                    node.line,
                    partial=False,
                    external=variable in package_state,
                )
            )

    return found


def definitely_assigned(
    cfg: Cfg, definitions: dict[int, set[str]], iteration_cap: int
) -> dict[int, frozenset[str]]:
    """Variables assigned on EVERY route to each node.

    A "must" analysis: intersection at merge points rather than union. Where reaching
    definitions answers "could this have been assigned", this answers "was it certainly
    assigned" - and the gap between the two is exactly the partial case worth reporting.

    Computed as a fixed point rather than by walking paths. Enumerating routes through a
    CFG with branches and exception edges is exponential; this is linear in the graph and
    gives the same answer.
    """
    everything = frozenset().union(*definitions.values()) if definitions else frozenset()

    # Optimistic start: assume everything is assigned everywhere, then let the entry and
    # the intersections cut it back. Standard for a "must" analysis.
    incoming: dict[int, frozenset[str]] = {n: everything for n in cfg.nodes}
    outgoing: dict[int, frozenset[str]] = {n: everything for n in cfg.nodes}
    incoming[cfg.entry] = frozenset()
    outgoing[cfg.entry] = frozenset(definitions.get(cfg.entry, set()))

    order = sorted(cfg.nodes)
    for _ in range(iteration_cap):
        changed = False
        for node_id in order:
            if node_id == cfg.entry:
                continue
            predecessors = cfg.predecessors(node_id)
            if predecessors:
                merged = everything
                for edge in predecessors:
                    merged &= outgoing[edge.source]
            else:
                merged = frozenset()

            if merged != incoming[node_id]:
                incoming[node_id] = merged
                changed = True

            after = merged | frozenset(definitions.get(node_id, set()))
            if after != outgoing[node_id]:
                outgoing[node_id] = after
                changed = True
        if not changed:
            break

    return incoming
