# Batch B5 — Semi-structured data: objective cards

**Status:** drafted 2026-10-03. Decisions D-29 and D-30 were taken before any key was
written. The SQL and keys were written blind and then run; results are in `REPORT.md`.

**Scope (programme plan §1, row B5):**

- jsonb operators: `->`, `->>`, `#>`, `#>>`, subscripts, `@>`, `<@`, `?`, `?|`, `?&`,
  `@?`, `@@`, `||` and `-`;
- jsonb builders: `jsonb_build_object` / `_array`, `to_jsonb`, `jsonb_set`, and PostgreSQL
  16/17's `JSON_OBJECT`, `JSON_VALUE`, `JSON_QUERY` and `IS JSON`;
- jsonb aggregates: `jsonb_agg`, `jsonb_object_agg`, with and without ORDER BY;
- jsonb set-returning functions, deferred here from B6: `jsonb_array_elements(_text)`,
  `jsonb_each(_text)`, `jsonb_object_keys`, `jsonb_to_record(set)`, `jsonb_path_query`;
- `jsonb_path_query_first` / `_exists`;
- `JSON_TABLE` (PostgreSQL 17) and `XMLTABLE`;
- `xpath()` and the xml builders (`xmlelement`, `xmlforest`, `xmlagg`);
- hstore: `->`, `?`, `@>`, `akeys`, `each()`, `hstore(k, v)`.

**Schema change (decision B5-QC, additive only).** `staging.feed_xml` (`doc xml`) and
`staging.device_attrs` (`attrs hstore`) were added. No existing object changed, and every
B1–B9 pin passes after the rebuild. The jsonb columns used are `staging.orders.payload`,
`staging.customers.attributes`, `staging.products.dims`, `staging.shipments.tracking` and
`staging.web_events.properties`.

Every node is schema-qualified (D-25), and every earlier convention applies.

## How the existing conventions apply

- **Extraction is derived** (D-29 below). The value a key or path picks from a document is
  computed from the document, as an element is from its array (PG-05).
- **A column used as a key, a path or a PASSING argument is a derived value** (D-22): in
  `o.payload ->> c.keyname`, both columns.
- **A builder reads every column it is given**, derived: `jsonb_build_object('k', o.a)` and
  `jsonb_set(o.payload, '{k}', to_jsonb(o.b))` read a and b, and payload and b.
- **An aggregate over a document is aggregated.** `jsonb_agg(x ORDER BY y)` makes y
  influence, as `string_agg`'s ordering does (B1).
- **A containment, existence, path-match or IS JSON test in WHERE is a filter** from every
  column it reads, documents included.
- **A JSON-returning set-returning function in FROM follows B6.** Its columns are derived
  from its argument columns (D-28), WITH ORDINALITY gives influence (D-26), and an unused
  one is structural (D-27).
- **Paths and keys written as literals are not sources.** `'$.items[*] ? (@.qty > 1)'`
  filters inside a document; the column edges are the document's.

## Decisions taken 2026-10-03, before any B5 key was written

### D-29 · B5-QA — lineage into and out of a document is column-level

The document column is the node. Any extraction, path or key reads the whole column, as a
**derived** value. A literal path is part of the expression, not of the node, as an array
subscript is. No path-level nodes are introduced.

### D-30 · B5-QB — JSON_TABLE and XMLTABLE are analysed

Their COLUMNS clause states the output shape, so it is decidable. Each output column is a
**derived** value of the document column and of every column in a PASSING clause. A
`FOR ORDINALITY` column gets **influence** from the document (D-26). `NESTED PATH` columns
are derived the same way. The refusal ("columns are projected out of a document") is
retired from the register for these two constructs.

## The cards

| Test | Objective | Statements |
|---|---|---|
| **T1 Breadth** | every operator, builder, aggregate, SRF, JSON_TABLE, XMLTABLE, xpath and hstore form once | ~18 |
| **T2 Depth** | documents built in one CTE and taken apart three levels up; JSON_TABLE feeding an aggregate; elements of elements; xml built then queried | ~7 |
| **T3 Interaction** | documents meeting B1–B4 rules: GROUP BY an extracted key, a window over a JSON_TABLE ordinality, ORDER BY a key under LIMIT (D-9), a path test inside a scalar subquery (D-12), an UPDATE … SET payload = jsonb_set(…), an ON CONFLICT accumulator into jsonb | ~8 |
| **T4 Adversarial** | a column named like a JSON key, a key that is a column name in a literal, `?` as an operator not a parameter, a document compared with itself, a literal-only document, an SRF over a literal, a key taken from another column | ~11 |
| **T5 Metamorphic** | must-equal pairs: `->>` vs `#>>`; `jsonb_extract_path_text` vs a `->` chain; `->` subscript vs `['k']`; `jsonb_array_elements` in FROM vs the select list; `JSON_TABLE` vs `jsonb_to_recordset`; `@>` vs `->>` equality in WHERE; `jsonb_agg` vs `array_to_json(array_agg)`; hstore `->` vs an hstore subscript. Then one realistic event-stream model | 8 pairs + 1 model |
