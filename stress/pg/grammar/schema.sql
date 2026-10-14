-- =====================================================================================
-- The PostgreSQL grammar checklist's schema (docs/pg-coverage.md).
--
-- Deliberately tiny and generic: every checklist snippet reads `src.a` / `src.b` and writes
-- `tgt.t`, so a snippet tests ONE construct and its expected lineage is one line - e.g.
-- `src.a.v -> tgt.t.v`. The columns cover the types PostgreSQL's grammar has special
-- syntax for (json, xml, arrays, ranges, geometry, intervals).
--
-- `dictionary.json` beside this file is GENERATED from it:
--   .\.venv\Scripts\python.exe stress/pg/build_dictionary.py --schema stress/pg/grammar/schema.sql \
--       --out stress/pg/grammar/dictionary.json --default-schema src
-- =====================================================================================

CREATE SCHEMA src;
CREATE SCHEMA tgt;

CREATE TABLE src.a (
    id   integer PRIMARY KEY,
    k    integer,
    v    numeric,
    w    numeric,
    n    text,
    d    date,
    ts   timestamptz,
    iv   interval,
    b    boolean,
    j    jsonb,
    x    xml,
    arr  integer[],
    rg   int4range,
    g    point
);

CREATE TABLE src.b (
    id   integer PRIMARY KEY,
    k    integer,
    v    numeric,
    n    text,
    d    date
);

CREATE TABLE tgt.t (
    id   integer,
    k    integer,
    v    numeric,
    w    numeric,
    n    text,
    d    date,
    ts   timestamptz,
    iv   interval,
    b    boolean,
    j    jsonb,
    x    xml,
    arr  integer[],
    rg   int4range,
    g    point
);

CREATE VIEW src.va AS SELECT a.id, a.k, a.v FROM src.a AS a;
