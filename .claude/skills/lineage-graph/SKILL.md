---
name: lineage-graph
description: How to read this project's SQL lineage output and Neo4j lineage graph - flows (value / filter / influence), transforms, filter phase, boundaries, refusals, node ids and graph schema. Use before answering any question about lineage, edges, boundaries or refusals, or before querying the lineage graph.
---

# Lineage graph semantics

Read the single source of truth before answering:
[src/lineage/semantics/lineage_semantics.md](../../../src/lineage/semantics/lineage_semantics.md)

- Do not restate or copy it here; it is versioned with the parser and the graph schema.
- Query the graph only through the typed tools in `src/lineage/graph/tools.py`
  (`lineage graph trace` from the CLI), never by guessing lineage from names.
