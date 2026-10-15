"""Objects a PostgreSQL script creates, visible to the statements after it (D-31, batch B7).

The dictionary is a snapshot taken before the script runs, so a table, view or temp table
the script creates did not exist for the analyser: a later read of it was "relation not in
dictionary", the created object was named bare (`ch_rev`, not `mart.ch_rev` - finding
B7-01), and an unqualified name that a TEMP table had taken bound to the real table of
that name instead (B7-04: an INSERT into a temp `orders` reported as a write into
`staging.orders`).

`ScriptCatalog` keeps an OVERLAY of the dictionary and updates it statement by statement:
CREATE registers (with its schema, its columns and, for a view, its definition), DROP
removes. Every resolver already reads the dictionary, so the overlay is all they need:

* a VIEW is registered with its text, so `band0._inline_views` inlines it (D-31);
* a TABLE, temp table or materialized view is registered as a table - a node;
* a temp table's schema is `pg_temp`, which `relations.schema_for` puts first.

It also says what the DDL itself contributes:

* SELECT ... INTO is restated as CREATE TABLE AS (B7-02);
* CREATE TABLE IF NOT EXISTS on an existing table, and WITH NO DATA, write no rows
  (B7-05, B7-06);
* a plain CREATE TABLE is counted; its GENERATED columns derive from the columns they read
  (D-33);
* PARTITION OF, INHERITS and ATTACH PARTITION flow child -> parent, identity (D-34);
* REFRESH re-runs a materialized view's known query (D-32).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

from lineage.analysis.relations import SEARCH_PATH, identifier
from lineage.analysis.transforms import transform_of_path
from lineage.ir.model import Transform
from lineage.parsing.pg_grammar import NO_DATA_MARKER
from lineage.parsing.sqlscript import skip_lexeme
from lineage.resolution.dictionary import Dictionary, ObjectInfo, fold

Fact = tuple[str, str, str, str, Transform]
"""(source relation, source column, target relation, target column, transform)."""

# CREATE kinds that define something holding no rows of its own (finding F0-04).
_NO_ROWS = frozenset({"INDEX", "SEQUENCE", "SCHEMA", "TYPE"})

_REFRESH = re.compile(
    r"^\s*REFRESH\s+MATERIALIZED\s+VIEW\s+(?:CONCURRENTLY\s+)?(?P<name>[\w.\"]+)",
    re.IGNORECASE,
)
_PREPARE = re.compile(
    r"^\s*PREPARE\s+(?P<name>[\w\"]+)\s*(?:\([^)]*\))?\s+AS\s+(?P<body>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_EXECUTE = re.compile(
    r"^\s*(?P<create>CREATE\s+(?:(?:LOCAL|GLOBAL)\s+)?(?:(?:TEMP|TEMPORARY|UNLOGGED)\s+)?"
    r"TABLE\s+.*?\s+AS\s+)?EXECUTE\s+(?P<name>[\w\"]+)\s*(?:\(.*\))?\s*$",
    re.IGNORECASE | re.DOTALL,
)
_DEALLOCATE = re.compile(r"^\s*DEALLOCATE\s+(?:PREPARE\s+)?(?P<name>[\w\"]+)\s*$", re.IGNORECASE)
_ATTACH = re.compile(
    r"^\s*ALTER\s+TABLE\s+(?:ONLY\s+)?(?P<parent>[\w.\"]+)\s+(?P<verb>ATTACH|DETACH)\s+"
    r"PARTITION\s+(?P<child>[\w.\"]+)",
    re.IGNORECASE,
)


@dataclass
class Effect:
    """What the analyser does with one statement, decided by the catalog."""

    rewrite: str | None = None
    """Analyse this text instead of the statement's own."""
    facts: list[Fact] = field(default_factory=list)
    """Edges the DDL itself contributes; the statement is counted and not analysed further."""
    count_only: bool = False
    """Counted, with no edges beyond `facts`, and not analysed further."""
    skip: bool = False
    """Catalog bookkeeping only (DROP): not counted, not analysed."""
    unknown: str | None = None
    """A REFRESH (D-32) or EXECUTE (D-35) of a definition that is not known."""
    unknown_detail: str = ""
    """Why `unknown` is unknown, for the boundary's sentence."""
    finalize: Any = None
    """Run after the statement is analysed (a view becomes inlinable only afterwards)."""


class ScriptCatalog:
    """The dictionary as the script has changed it so far."""

    def __init__(self, dictionary: Dictionary) -> None:
        self.dictionary = dictionary
        self._matviews: dict[str, tuple[list[str], str]] = {}
        self._prepared: dict[str, str] = {}

    # --- the decision -------------------------------------------------------------------

    def effect(self, text: str) -> Effect | None:
        prepared = self._prepared_statement(_lead(text))
        if prepared is not None:
            return prepared
        if re.match(r"(?i)^CALL\b", _lead(text)):
            # D-39: a routine's lineage is at its definition, so a CALL adds none - but it
            # is a statement, and it was absent from coverage (B8-02).
            return Effect(count_only=True)
        refresh = _REFRESH.match(_code(text))
        if refresh:
            return self._refresh(refresh.group("name"))
        attach = _ATTACH.match(_code(text))
        if attach:
            return self._attach(attach.group("parent"), attach.group("child"), attach.group("verb"))
        try:
            parsed = sqlglot.parse_one(text, read="postgres")
        except Exception:
            return None
        if isinstance(parsed, exp.Select) and parsed.args.get("into") is not None:
            return self._select_into(parsed)
        if isinstance(parsed, exp.Drop):
            self._drop(parsed)
            return Effect(skip=True)
        if isinstance(parsed, exp.Alter):
            self._alter(parsed)
            return None  # what the ALTER does to the table is an effect (D-62), as before
        if isinstance(parsed, exp.Create):
            kind = (parsed.args.get("kind") or "").upper()
            if kind in _NO_ROWS:
                # Finding F0-04: these move no data, as a plain CREATE TABLE does not (D-33),
                # and were refused as "CREATE TABLE without AS SELECT". Counted, no lineage -
                # a partial index's WHERE chooses which rows are INDEXED, not which exist.
                return Effect(count_only=True)
            if kind == "TABLE":
                return self._create_table(parsed)
            if kind == "VIEW":
                return self._create_view(parsed, NO_DATA_MARKER in text)
        return None

    # --- the forms ----------------------------------------------------------------------

    def _select_into(self, select: exp.Select) -> Effect:
        into = select.args["into"]
        temp = bool(into.args.get("temporary"))
        query = select.copy()
        query.set("into", None)
        name = self._key(into.this, temp)
        text = f"CREATE TABLE {_quoted(name)} AS {query.sql(dialect='postgres')}"
        self._register(name, self._query_columns(query, []), "TABLE", temp)
        return Effect(rewrite=text)

    def _create_table(self, create: exp.Create) -> Effect | None:
        target = create.this
        schema = target if isinstance(target, exp.Schema) else None
        table = schema.this if schema is not None else target
        temp = _has(create, exp.TemporaryProperty)
        name = self._key(table, temp)
        if create.args.get("exists") and name in self.dictionary.objects:
            return Effect(count_only=True)  # IF NOT EXISTS on an existing table (B7-05)

        query = create.expression
        if isinstance(query, exp.Query):
            listed = [identifier(c, "postgres") for c in schema.expressions] if schema else []
            self._register(name, self._query_columns(query, listed), "TABLE", temp)
            no_data = _property(create, exp.WithDataProperty)
            if no_data is not None and no_data.args.get("no"):
                return Effect(count_only=True)  # WITH NO DATA moves no rows (B7-06)
            return None  # an ordinary CTAS: band 0 analyses it against the new entry

        own: list[str] = []
        facts: list[Fact] = []
        generated: list[tuple[str, Any]] = []
        for item in schema.expressions if schema is not None else []:
            if isinstance(item, exp.LikeProperty):
                own.extend(self._columns(self._key(item.this, False)))
            elif isinstance(item, exp.ColumnDef):
                column = identifier(item.this, "postgres")
                own.append(column)
                for constraint in item.args.get("constraints") or []:
                    computed = constraint.args.get("kind")
                    if isinstance(computed, exp.ComputedColumnConstraint):
                        generated.append((column, computed.this))
        parent_columns: list[str] = []
        parent: str | None = None
        partition_of = _property(create, exp.PartitionedOfProperty)
        inherits = _property(create, exp.InheritsProperty)
        if partition_of is not None:
            parent = self._key(partition_of.this, False)
            parent_columns = self._columns(parent)
            own = parent_columns + [c for c in own if c not in parent_columns]
        elif inherits is not None and inherits.expressions:
            parent = self._key(inherits.expressions[0], False)
            parent_columns = self._columns(parent)
            own = parent_columns + [c for c in own if c not in parent_columns]
        self._register(name, own, "TABLE", temp)

        # D-33: a generated column derives from the columns its expression reads.
        for column, expression in generated:
            for read in expression.find_all(exp.Column):
                facts.append(
                    (
                        name,
                        identifier(read.this, "postgres"),
                        name,
                        column,
                        transform_of_path(expression, read),
                    )
                )
        # D-34: a partition's or child's rows are its parent's.
        if parent is not None:
            facts.extend((name, c, parent, c, Transform.IDENTITY) for c in parent_columns)
        return Effect(facts=facts, count_only=True)

    def _create_view(self, create: exp.Create, no_data: bool = False) -> Effect | None:
        target = create.this
        schema = target if isinstance(target, exp.Schema) else None
        table = schema.this if schema is not None else target
        name = self._key(table, _has(create, exp.TemporaryProperty))
        query = create.expression
        if not isinstance(query, exp.Query):
            return None
        listed = [identifier(c, "postgres") for c in schema.expressions] if schema else []
        columns = self._query_columns(query, listed)
        # Registered as a TABLE while its own CREATE is analysed, so it is a target with a
        # name and columns - never inlined into itself.
        self._register(name, columns, "TABLE", False)
        if _has(create, exp.MaterializedProperty):
            self._matviews[name] = (columns, query.sql(dialect="postgres"))
            # WITH NO DATA: SQLGlot cannot read it on a materialized view, so pg_grammar
            # leaves a marker in its place (F0-01); the parsed property is kept for a reader
            # that one day can.
            parsed_no_data = _property(create, exp.WithDataProperty)
            if no_data or (parsed_no_data is not None and parsed_no_data.args.get("no")):
                return Effect(count_only=True)
            return None

        body = query.sql(dialect="postgres")
        if listed:
            inner = ", ".join(_quoted_ident(c) for c in listed)
            body = f"SELECT * FROM ({body}) AS __view({inner})"

        def finalize() -> None:
            self._register(name, columns, "VIEW", False, body)

        return Effect(finalize=finalize)

    def _prepared_statement(self, code: str) -> Effect | None:
        """D-35: PREPARE stores, EXECUTE re-analyses at its own line, DEALLOCATE forgets."""
        prepare = _PREPARE.match(code)
        if prepare:
            self._prepared[_name(prepare.group("name"))] = prepare.group("body")
            return Effect(count_only=True)
        deallocate = _DEALLOCATE.match(code)
        if deallocate:
            name = _name(deallocate.group("name"))
            if name == "all" and not deallocate.group("name").startswith('"'):
                self._prepared.clear()
            else:
                self._prepared.pop(name, None)
            return Effect(count_only=True)
        execute = _EXECUTE.match(code)
        if not execute:
            return None
        name = _name(execute.group("name"))
        body = self._prepared.get(name)
        if body is None:
            return Effect(
                count_only=True,
                unknown=name,
                unknown_detail=f"EXECUTE {name}: no PREPARE of it is in force at this point "
                "of the script, so what it runs cannot be named (D-35)",
            )
        # CREATE TABLE t AS EXECUTE p is CREATE TABLE t AS <p's query>.
        text = (execute.group("create") or "") + body
        inner = self.effect(text)
        if inner is None:
            return Effect(rewrite=text)
        if inner.rewrite is None and not (inner.count_only or inner.skip):
            return Effect(rewrite=text, finalize=inner.finalize)
        return inner

    def _refresh(self, written: str) -> Effect:
        name = self._key_from_text(written)
        known = self._matviews.get(name)
        if known is None:
            return Effect(
                count_only=True,
                unknown=name,
                unknown_detail=f"REFRESH MATERIALIZED VIEW {name}: its definition is not in "
                "this analysis, so what it writes cannot be named (D-32)",
            )
        columns, body = known
        target = f"{_quoted(name)} ({', '.join(_quoted_ident(c) for c in columns)})"
        return Effect(rewrite=f"INSERT INTO {target} {body}")

    def _attach(self, parent_text: str, child_text: str, verb: str) -> Effect:
        if verb.upper() == "DETACH":
            return Effect(count_only=True)
        parent = self._key_from_text(parent_text)
        child = self._key_from_text(child_text)
        shared = [c for c in self._columns(child) if c in set(self._columns(parent))]
        return Effect(
            facts=[(child, c, parent, c, Transform.IDENTITY) for c in shared], count_only=True
        )

    def _drop(self, drop: exp.Drop) -> None:
        for table in drop.find_all(exp.Table):
            name = self._key(table, False)
            temp_name = f"pg_temp.{identifier(table.this, 'postgres')}"
            for key in (temp_name, name):
                if key in self.dictionary.objects:
                    self._forget(key)
                    break

    def _alter(self, alter: exp.Alter) -> None:
        """D-65: a column added, dropped or renamed - or a table renamed - by the script.

        `ALTER TABLE staging.a ADD COLUMN email_domain text` then `INSERT ... email_domain`:
        the dictionary is the snapshot from before the script, so the column did not exist
        for the analyser and its lineage was declared "not a column" (holdout3, R1).
        """
        table = alter.this
        if not isinstance(table, exp.Table) or (alter.args.get("kind") or "").upper() != "TABLE":
            return
        name = self._key(table, False)
        if name not in self.dictionary.objects:
            return
        columns = self._columns(name)
        renamed: str | None = None
        for action in alter.args.get("actions") or []:
            if isinstance(action, exp.ColumnDef):
                column = identifier(action.this, "postgres")
                if column not in columns:
                    columns.append(column)
            elif isinstance(action, exp.Drop) and (action.args.get("kind") or "") == "COLUMN":
                named = [action.this] if action.this else action.args.get("tables") or []
                dropped = {identifier(c.this, "postgres") for c in named}
                columns = [c for c in columns if c not in dropped]
            elif isinstance(action, exp.RenameColumn):
                old = identifier(action.this.this, "postgres")
                new = identifier(action.args["to"].this, "postgres")
                columns = [new if c == old else c for c in columns]
            elif isinstance(action, exp.AlterRename):
                owner = name.partition(".")[0]
                renamed = f"{owner}.{identifier(action.this.this, 'postgres')}"
        info = self.dictionary.objects[name]
        temp = self.dictionary.temporary.get(name, False)
        text = self.dictionary.view_text.get(name)
        if renamed is not None:
            self._forget(name)
            name = renamed
        self._register(name, columns, info.object_type, temp, text)

    # --- the overlay --------------------------------------------------------------------

    def _register(
        self, name: str, columns: list[str], kind: str, temp: bool, text: str | None = None
    ) -> None:
        owner, _, table = name.partition(".")
        objects = dict(self.dictionary.objects)
        objects[name] = ObjectInfo(owner=owner, name=table, object_type=kind)
        view_text = dict(self.dictionary.view_text)
        if text is not None:
            view_text[name] = text
        else:
            view_text.pop(name, None)
        self.dictionary = self.dictionary.model_copy(
            update={
                "objects": objects,
                "columns": {**self.dictionary.columns, name: list(columns)},
                "view_text": view_text,
                "temporary": {**self.dictionary.temporary, name: temp},
            }
        )

    def _forget(self, name: str) -> None:
        self.dictionary = self.dictionary.model_copy(
            update={
                "objects": {k: v for k, v in self.dictionary.objects.items() if k != name},
                "columns": {k: v for k, v in self.dictionary.columns.items() if k != name},
                "view_text": {k: v for k, v in self.dictionary.view_text.items() if k != name},
                "temporary": {k: v for k, v in self.dictionary.temporary.items() if k != name},
            }
        )
        self._matviews.pop(name, None)

    def _columns(self, name: str) -> list[str]:
        return list(self.dictionary.columns.get(name, []))

    def _key(self, table: Any, temp: bool) -> str:
        """`schema.table` for a created or referenced relation (D-25, D-23, D-24, D-31)."""
        name = identifier(table.this, "postgres")
        if temp:
            return f"pg_temp.{name}"
        if table.args.get("db"):
            return f"{identifier(table.args['db'], 'postgres')}.{name}"
        if f"pg_temp.{name}" in self.dictionary.objects:
            return f"pg_temp.{name}"  # pg_temp is searched first
        path = SEARCH_PATH.get()
        if path:
            for schema in path:
                if f"{schema}.{name}" in self.dictionary.objects:
                    return f"{schema}.{name}"
            return f"{path[0]}.{name}"
        default = fold(self.dictionary.default_schema, "postgres")
        return f"{default}.{name}"

    def _key_from_text(self, text: str) -> str:
        table = sqlglot.parse_one(f"SELECT 1 FROM {text}", read="postgres").find(exp.Table)
        return self._key(table, False)

    def _query_columns(self, query: Any, listed: list[str]) -> list[str]:
        if listed:
            return listed
        selects = getattr(query, "selects", None) or []
        if any(isinstance(s, exp.Star) or isinstance(s.this, exp.Star) for s in selects):
            from lineage.analysis import band0  # the qualifier lives there; imported late

            try:
                qualified = band0._qualify(query.copy(), self.dictionary)
                selects = qualified.selects
            except Exception:
                return []
        return [s.alias_or_name for s in selects if s.alias_or_name]


def _has(create: exp.Create, kind: type) -> bool:
    return _property(create, kind) is not None


def _property(create: exp.Create, kind: type) -> Any:
    properties = create.args.get("properties")
    for item in properties.expressions if properties is not None else []:
        if isinstance(item, kind):
            return item
    return None


def _quoted(name: str) -> str:
    return ".".join(_quoted_ident(part) for part in name.split(".", 1))


def _quoted_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _name(text: str) -> str:
    """A prepared statement's name, folded as PostgreSQL folds an identifier."""
    return text[1:-1].replace('""', '"') if text.startswith('"') else text.lower()


def _lead(text: str) -> str:
    """The statement without the comments in front of it, block comments included."""
    index = 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        if not text.startswith(("--", "/*"), index):
            return text[index:]
        index = skip_lexeme(text, index) or len(text)


def _code(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("--"))
