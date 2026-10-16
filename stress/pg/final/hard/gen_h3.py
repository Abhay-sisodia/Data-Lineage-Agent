"""Write h3_entity_pipeline.sql: 60 stage CTEs in four repeating shapes (final suite, H3).

    .\\.venv\\Scripts\\python.exe stress/pg/final/hard/gen_h3.py

The stages are generated so that every repetition is exact; the key is written by hand.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
STAGES = 60

HEADER = """\
-- =========================================================================================
-- H3 · entity pipeline at depth: view -> materialized view -> load (hard)
--                                                           final suite, written 2026-10-07
-- -----------------------------------------------------------------------------------------
-- Shape: the entity-pipeline sample - a seed, then 60 stage CTEs that each carry `p.*` and
-- add one thing - as a VIEW, a MATERIALIZED VIEW over the view, an INSERT that reads both,
-- and a REFRESH. PostgreSQL 17. Four stage shapes repeat in order:
--   A (i % 4 = 1) a CASE adjustment and a WHERE on the carried amount ..... derived, filter
--   B (i % 4 = 2) LEFT JOIN LATERAL one-row aggregate over evt.activity ... D-14 aggregated
--   C (i % 4 = 3) LEFT JOIN LATERAL ... ORDER BY ... LIMIT 1 over evt.flag  D-14, D-13
--   D (i % 4 = 0) DISTINCT ON re-dedup of the carried rows ................ D-9 filters
-- What the file tests: `p.*` carried 60 levels; columns added at stage 1 and read at the
-- end; the same facts from 15 repetitions collapsing to one each; a view inlined into the
-- statements that read it, a materialized view read as a node (D-31); REFRESH (D-32).
-- Expected: edges only. No refusal, no boundary.
-- =========================================================================================
CREATE VIEW mart.v_entity_pipeline AS
WITH
seed AS (
    SELECT DISTINCT ON (e.entity_id)
           e.entity_id, e.parent_id, e.status, e.amount, e.display_name, e.payload, e.created_at
    FROM evt.entity_event AS e
    WHERE e.created_at >= TIMESTAMPTZ '2024-01-01 00:00:00+00'
    ORDER BY e.entity_id, e.created_at DESC, e.ingested_at DESC
),
"""


def stage(i: int) -> str:
    previous = "seed" if i == 1 else f"stage_{i - 1:02d}"
    name = f"stage_{i:02d}"
    kind = i % 4
    if kind == 1:
        return f"""\
{name} AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_{i}
    FROM {previous} AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
"""
    if kind == 2:
        return f"""\
{name} AS (
    SELECT p.*, a.act_total_{i}, a.act_count_{i}
    FROM {previous} AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_{i}, COUNT(*) AS act_count_{i}
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K{i}'
    ) AS a ON TRUE
),
"""
    if kind == 3:
        return f"""\
{name} AS (
    SELECT p.*, f.flag_{i}, f.flag_at_{i}
    FROM {previous} AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_{i}, g.active_at AS flag_at_{i}
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
"""
    return f"""\
{name} AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM {previous} AS p
    ORDER BY p.entity_id, p.created_at DESC
){',' if i < STAGES else ''}
"""


TAIL = """\
SELECT
    s.entity_id,
    s.parent_id,
    s.status,
    s.display_name,
    s.amount,
    s.adj_1,
    s.adj_57,
    s.act_total_2,
    s.act_total_58,
    s.act_count_58,
    s.flag_3,
    s.flag_at_59,
    COALESCE(s.act_total_2, 0) + COALESCE(s.act_total_58, 0)            AS act_total_sum,
    CASE WHEN s.flag_59 IS NOT NULL AND s.adj_57 > 0 THEN 'FLAGGED'
         ELSE 'CLEAR' END                                               AS state,
    s.payload ->> 'tier'                                                AS tier
FROM stage_60 AS s;

CREATE MATERIALIZED VIEW mart.mv_entity_status AS
SELECT v.status,
       COUNT(*)              AS entities,
       SUM(v.act_total_sum)  AS act_total,
       AVG(v.adj_57)         AS avg_adj,
       MAX(v.flag_at_59)     AS last_flag_at
FROM mart.v_entity_pipeline AS v
WHERE v.status <> 'DELETED'
GROUP BY v.status;

INSERT INTO mart.entity_state (entity_id, status, amount, activity_total, flag_count, last_seen)
SELECT v.entity_id,
       v.status,
       v.amount,
       v.act_total_sum,
       (SELECT COUNT(*)
        FROM evt.flag AS f2
        WHERE f2.entity_id = v.entity_id
          AND f2.active_flag),
       m.last_flag_at
FROM mart.v_entity_pipeline AS v
JOIN mart.mv_entity_status  AS m ON m.status = v.status;

REFRESH MATERIALIZED VIEW mart.mv_entity_status;
"""


def main() -> None:
    text = HEADER + "".join(stage(i) for i in range(1, STAGES + 1)) + TAIL
    (HERE / "h3_entity_pipeline.sql").write_text(text, encoding="utf-8", newline="\n")
    print(text.count("\n"), "lines")


if __name__ == "__main__":
    main()
