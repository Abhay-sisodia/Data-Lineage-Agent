-- =====================================================================================
-- B11 · T3 · SEMI-STRUCTURED — a fact build from document input, end to end
--
-- Objective card: stress/pg/b11_integration/OBJECTIVES.md.
-- Schema:         stress/pg/schema.sql. PostgreSQL: 17.
--
-- A new partition (D-34) loaded from jsonb extractions (D-29); unnest WITH ORDINALITY
-- (D-26, D-27); jsonb_to_recordset; a trigger on the fact (D-41); a read of the
-- partitioned parent; a containment-filtered product feed. Every earlier decision
-- applies.
-- =====================================================================================


-- -------------------------------------------------------------------------------------
-- S1 · this year's partition of the web events
-- -------------------------------------------------------------------------------------
CREATE TABLE staging.web_events_2026 PARTITION OF staging.web_events
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');


-- -------------------------------------------------------------------------------------
-- S2 · load it from the orders' documents
-- -------------------------------------------------------------------------------------
INSERT INTO staging.web_events_2026 (id, customer_id, event_type, event_ts, page, properties)
SELECT o.id,
       o.customer_id,
       o.payload ->> 'event',
       o.order_ts,
       o.payload #>> '{web,page}',
       o.payload -> 'props'
FROM staging.orders AS o
WHERE o.payload ? 'event';


-- -------------------------------------------------------------------------------------
-- S3 · the first three tags of each order, counted
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, segment, orders)
SELECT 'tag', t.tag, COUNT(*)
FROM staging.orders AS o
CROSS JOIN LATERAL unnest(o.tags) WITH ORDINALITY AS t(tag, pos)
WHERE t.pos <= 3
GROUP BY t.tag;


-- R4 — a trigger on the shipment fact, maintaining the carrier scorecard
CREATE FUNCTION mart.trg_ship_score() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_r4 integer;
BEGIN
-- S4 · the trigger body: NEW is the fact's row (D-41)
    INSERT INTO mart.carrier_scorecard (carrier_id, shipments)
    VALUES (NEW.carrier_id, 1);
    RETURN NEW;
END;
$$;

CREATE TRIGGER t_ship_score
AFTER INSERT ON core.fct_shipment
FOR EACH ROW EXECUTE FUNCTION mart.trg_ship_score();


-- -------------------------------------------------------------------------------------
-- S5 · the shipment fact, from each shipment's tracking document
-- -------------------------------------------------------------------------------------
INSERT INTO core.fct_shipment (shipment_id, carrier_id, weight_kg, status)
SELECT s.id, x.carrier, x.kg, s.status
FROM staging.shipments AS s
CROSS JOIN LATERAL jsonb_to_recordset(s.tracking) AS x(carrier integer, kg numeric)
WHERE s.status <> 'LOST';


-- -------------------------------------------------------------------------------------
-- S6 · read back through the partitioned parent
-- -------------------------------------------------------------------------------------
INSERT INTO mart.kpi_rollup (level, customers)
SELECT w.event_type, COUNT(DISTINCT w.customer_id)
FROM staging.web_events AS w
WHERE w.event_ts >= DATE '2026-01-01'
GROUP BY w.event_type;


-- -------------------------------------------------------------------------------------
-- S7 · the product feed: a containment filter and an extraction
-- -------------------------------------------------------------------------------------
INSERT INTO mart.product_performance (product_id, category_name)
SELECT p.id, p.dims ->> 'category'
FROM staging.products AS p
WHERE p.dims @> '{"active": true}';
