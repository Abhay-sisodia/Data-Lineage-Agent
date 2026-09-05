-- Band 1 / IF and CASE
-- Why it's hard: different branches write the same column from different sources.
-- Approach: union of paths, each edge tagged with the guard that governs it.
--
-- The guard is not a footnote - it is part of the fact. Outside the EU this column
-- is populated from a completely different source, and a regulator will ask exactly
-- that. An unguarded edge here would be a wrong answer wearing a correct shape.
--
-- Note the nested branch: the APAC path is guarded by TWO conditions conjoined.

CREATE OR REPLACE PROCEDURE b1_if_case(p_region VARCHAR2, p_strict NUMBER) IS
BEGIN
    IF p_region = 'EU' THEN
        UPDATE dim_customer d
           SET d.is_active = 1
         WHERE d.cust_id IN (SELECT cust_id FROM tmp_recent);

    ELSIF p_region = 'APAC' THEN
        IF p_strict = 1 THEN
            UPDATE dim_customer d
               SET d.is_active = (SELECT MAX(status_code)
                                    FROM stg_customer s
                                   WHERE s.cust_id = d.cust_id)
             WHERE d.region = 'APAC';
        END IF;

    ELSE
        UPDATE dim_customer d
           SET d.is_active = CASE
                                 WHEN d.lifetime_value > 1000 THEN 1
                                 ELSE 0
                             END;
    END IF;

    COMMIT;
END b1_if_case;
/
