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
stage_01 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_1
    FROM seed AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_02 AS (
    SELECT p.*, a.act_total_2, a.act_count_2
    FROM stage_01 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_2, COUNT(*) AS act_count_2
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K2'
    ) AS a ON TRUE
),
stage_03 AS (
    SELECT p.*, f.flag_3, f.flag_at_3
    FROM stage_02 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_3, g.active_at AS flag_at_3
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_04 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_03 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_05 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_5
    FROM stage_04 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_06 AS (
    SELECT p.*, a.act_total_6, a.act_count_6
    FROM stage_05 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_6, COUNT(*) AS act_count_6
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K6'
    ) AS a ON TRUE
),
stage_07 AS (
    SELECT p.*, f.flag_7, f.flag_at_7
    FROM stage_06 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_7, g.active_at AS flag_at_7
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_08 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_07 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_09 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_9
    FROM stage_08 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_10 AS (
    SELECT p.*, a.act_total_10, a.act_count_10
    FROM stage_09 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_10, COUNT(*) AS act_count_10
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K10'
    ) AS a ON TRUE
),
stage_11 AS (
    SELECT p.*, f.flag_11, f.flag_at_11
    FROM stage_10 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_11, g.active_at AS flag_at_11
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_12 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_11 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_13 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_13
    FROM stage_12 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_14 AS (
    SELECT p.*, a.act_total_14, a.act_count_14
    FROM stage_13 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_14, COUNT(*) AS act_count_14
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K14'
    ) AS a ON TRUE
),
stage_15 AS (
    SELECT p.*, f.flag_15, f.flag_at_15
    FROM stage_14 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_15, g.active_at AS flag_at_15
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_16 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_15 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_17 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_17
    FROM stage_16 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_18 AS (
    SELECT p.*, a.act_total_18, a.act_count_18
    FROM stage_17 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_18, COUNT(*) AS act_count_18
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K18'
    ) AS a ON TRUE
),
stage_19 AS (
    SELECT p.*, f.flag_19, f.flag_at_19
    FROM stage_18 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_19, g.active_at AS flag_at_19
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_20 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_19 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_21 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_21
    FROM stage_20 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_22 AS (
    SELECT p.*, a.act_total_22, a.act_count_22
    FROM stage_21 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_22, COUNT(*) AS act_count_22
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K22'
    ) AS a ON TRUE
),
stage_23 AS (
    SELECT p.*, f.flag_23, f.flag_at_23
    FROM stage_22 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_23, g.active_at AS flag_at_23
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_24 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_23 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_25 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_25
    FROM stage_24 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_26 AS (
    SELECT p.*, a.act_total_26, a.act_count_26
    FROM stage_25 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_26, COUNT(*) AS act_count_26
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K26'
    ) AS a ON TRUE
),
stage_27 AS (
    SELECT p.*, f.flag_27, f.flag_at_27
    FROM stage_26 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_27, g.active_at AS flag_at_27
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_28 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_27 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_29 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_29
    FROM stage_28 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_30 AS (
    SELECT p.*, a.act_total_30, a.act_count_30
    FROM stage_29 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_30, COUNT(*) AS act_count_30
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K30'
    ) AS a ON TRUE
),
stage_31 AS (
    SELECT p.*, f.flag_31, f.flag_at_31
    FROM stage_30 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_31, g.active_at AS flag_at_31
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_32 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_31 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_33 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_33
    FROM stage_32 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_34 AS (
    SELECT p.*, a.act_total_34, a.act_count_34
    FROM stage_33 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_34, COUNT(*) AS act_count_34
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K34'
    ) AS a ON TRUE
),
stage_35 AS (
    SELECT p.*, f.flag_35, f.flag_at_35
    FROM stage_34 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_35, g.active_at AS flag_at_35
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_36 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_35 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_37 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_37
    FROM stage_36 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_38 AS (
    SELECT p.*, a.act_total_38, a.act_count_38
    FROM stage_37 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_38, COUNT(*) AS act_count_38
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K38'
    ) AS a ON TRUE
),
stage_39 AS (
    SELECT p.*, f.flag_39, f.flag_at_39
    FROM stage_38 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_39, g.active_at AS flag_at_39
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_40 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_39 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_41 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_41
    FROM stage_40 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_42 AS (
    SELECT p.*, a.act_total_42, a.act_count_42
    FROM stage_41 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_42, COUNT(*) AS act_count_42
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K42'
    ) AS a ON TRUE
),
stage_43 AS (
    SELECT p.*, f.flag_43, f.flag_at_43
    FROM stage_42 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_43, g.active_at AS flag_at_43
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_44 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_43 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_45 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_45
    FROM stage_44 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_46 AS (
    SELECT p.*, a.act_total_46, a.act_count_46
    FROM stage_45 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_46, COUNT(*) AS act_count_46
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K46'
    ) AS a ON TRUE
),
stage_47 AS (
    SELECT p.*, f.flag_47, f.flag_at_47
    FROM stage_46 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_47, g.active_at AS flag_at_47
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_48 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_47 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_49 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_49
    FROM stage_48 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_50 AS (
    SELECT p.*, a.act_total_50, a.act_count_50
    FROM stage_49 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_50, COUNT(*) AS act_count_50
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K50'
    ) AS a ON TRUE
),
stage_51 AS (
    SELECT p.*, f.flag_51, f.flag_at_51
    FROM stage_50 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_51, g.active_at AS flag_at_51
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_52 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_51 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_53 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_53
    FROM stage_52 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_54 AS (
    SELECT p.*, a.act_total_54, a.act_count_54
    FROM stage_53 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_54, COUNT(*) AS act_count_54
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K54'
    ) AS a ON TRUE
),
stage_55 AS (
    SELECT p.*, f.flag_55, f.flag_at_55
    FROM stage_54 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_55, g.active_at AS flag_at_55
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_56 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_55 AS p
    ORDER BY p.entity_id, p.created_at DESC
),
stage_57 AS (
    SELECT p.*,
           CASE WHEN p.amount > 1000 THEN p.amount * 1.05 ELSE p.amount END AS adj_57
    FROM stage_56 AS p
    WHERE p.amount IS NULL OR p.amount <> 'NaN'::numeric
),
stage_58 AS (
    SELECT p.*, a.act_total_58, a.act_count_58
    FROM stage_57 AS p
    LEFT JOIN LATERAL (
        SELECT SUM(x.amount) AS act_total_58, COUNT(*) AS act_count_58
        FROM evt.activity AS x
        WHERE x.entity_id = p.entity_id
          AND x.kind = 'K58'
    ) AS a ON TRUE
),
stage_59 AS (
    SELECT p.*, f.flag_59, f.flag_at_59
    FROM stage_58 AS p
    LEFT JOIN LATERAL (
        SELECT g.flag_code AS flag_59, g.active_at AS flag_at_59
        FROM evt.flag AS g
        WHERE g.entity_id = p.entity_id
        ORDER BY g.active_at DESC
        LIMIT 1
    ) AS f ON TRUE
),
stage_60 AS (
    SELECT DISTINCT ON (p.entity_id) p.*
    FROM stage_59 AS p
    ORDER BY p.entity_id, p.created_at DESC
)
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
