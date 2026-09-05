-- Shared base schema for the adversarial corpus.
--
-- Deliberately small, and deliberately shaped like the worked example in the spike
-- document: a policy table that silently controls a filter, a staging table, an
-- intermediate temp table, and a dimension carrying the regulated column.
--
-- Every adversarial package binds against these tables, so column-level lineage has
-- real columns to resolve to. Table-level lineage is the useless level; without DDL
-- that is all you can produce.

-- Reference / policy ---------------------------------------------------------

CREATE TABLE ref_policy (
    region        VARCHAR2(10)  NOT NULL,
    window_days   NUMBER(4)     NOT NULL,
    effective_from DATE         NOT NULL,
    CONSTRAINT pk_ref_policy PRIMARY KEY (region)
);

-- Staging --------------------------------------------------------------------

CREATE TABLE stg_customer (
    cust_id       NUMBER(12)    NOT NULL,
    region        VARCHAR2(10),
    last_login    DATE,
    email         VARCHAR2(200),
    signup_date   DATE,
    status_code   NUMBER(2),
    CONSTRAINT pk_stg_customer PRIMARY KEY (cust_id)
);

CREATE TABLE stg_orders (
    order_id      NUMBER(12)    NOT NULL,
    cust_id       NUMBER(12),
    order_date    DATE,
    gross_amount  NUMBER(14,2),
    discount_amt  NUMBER(14,2),
    currency      VARCHAR2(3),
    CONSTRAINT pk_stg_orders PRIMARY KEY (order_id)
);

-- Intermediate ---------------------------------------------------------------

CREATE TABLE tmp_recent (
    cust_id       NUMBER(12),
    last_login    DATE
);

-- Dimensions and facts -------------------------------------------------------

CREATE TABLE dim_customer (
    cust_id       NUMBER(12)    NOT NULL,
    region        VARCHAR2(10),
    is_active     NUMBER(1),
    email         VARCHAR2(200),
    lifetime_value NUMBER(14,2),
    CONSTRAINT pk_dim_customer PRIMARY KEY (cust_id)
);

CREATE TABLE fct_revenue (
    cust_id       NUMBER(12),
    period_month  DATE,
    net_amount    NUMBER(14,2),
    order_count   NUMBER(8)
);

-- Seed data so the packages can actually execute. Execution is what populates
-- V$SQL, which is what the log-recovery sub-spike reads.

INSERT INTO ref_policy (region, window_days, effective_from)
VALUES ('EU', 30, DATE '2024-01-01');
INSERT INTO ref_policy (region, window_days, effective_from)
VALUES ('US', 45, DATE '2024-01-01');
INSERT INTO ref_policy (region, window_days, effective_from)
VALUES ('APAC', 60, DATE '2024-01-01');

INSERT INTO stg_customer (cust_id, region, last_login, email, signup_date, status_code)
VALUES (1, 'EU', SYSDATE - 5, 'a@example.com', DATE '2023-03-01', 1);
INSERT INTO stg_customer (cust_id, region, last_login, email, signup_date, status_code)
VALUES (2, 'EU', SYSDATE - 90, 'b@example.com', DATE '2023-05-11', 7);
INSERT INTO stg_customer (cust_id, region, last_login, email, signup_date, status_code)
VALUES (3, 'US', SYSDATE - 2, 'c@example.com', DATE '2024-01-20', 1);
INSERT INTO stg_customer (cust_id, region, last_login, email, signup_date, status_code)
VALUES (4, 'APAC', SYSDATE - 400, 'd@example.com', DATE '2022-08-02', 3);

INSERT INTO stg_orders (order_id, cust_id, order_date, gross_amount, discount_amt, currency)
VALUES (100, 1, SYSDATE - 10, 250.00, 25.00, 'EUR');
INSERT INTO stg_orders (order_id, cust_id, order_date, gross_amount, discount_amt, currency)
VALUES (101, 1, SYSDATE - 3, 120.00, 0.00, 'EUR');
INSERT INTO stg_orders (order_id, cust_id, order_date, gross_amount, discount_amt, currency)
VALUES (102, 3, SYSDATE - 1, 900.00, 90.00, 'USD');

INSERT INTO dim_customer (cust_id, region, is_active, email, lifetime_value)
VALUES (1, 'EU', 0, 'a@example.com', 0);
INSERT INTO dim_customer (cust_id, region, is_active, email, lifetime_value)
VALUES (2, 'EU', 0, 'b@example.com', 0);
INSERT INTO dim_customer (cust_id, region, is_active, email, lifetime_value)
VALUES (3, 'US', 0, 'c@example.com', 0);
INSERT INTO dim_customer (cust_id, region, is_active, email, lifetime_value)
VALUES (4, 'APAC', 0, 'd@example.com', 0);

COMMIT;
