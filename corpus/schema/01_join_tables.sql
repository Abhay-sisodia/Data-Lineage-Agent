-- Extension schema for the complex-SQL band.
--
-- The original schema was shaped for PROCEDURAL difficulty - variables, branches,
-- package state. It has almost no join surface: the average statement in the band-0/1
-- corpus touches 0.7 tables and not one contains a JOIN.
--
-- These tables exist so SQL difficulty can be tested on its own axis: multi-table joins,
-- self-joins, outer joins, and the column-ambiguity that comes with them. Column names
-- are deliberately REPEATED across tables (cust_id, amount, status_code, created_at) -
-- that ambiguity is where silent binding errors live, and a corpus of uniquely named
-- columns would never surface them.

CREATE TABLE stg_products (
    product_id    NUMBER(12)   NOT NULL,
    product_name  VARCHAR2(80),
    category_id   NUMBER(6),
    list_price    NUMBER(14,2),
    status_code   NUMBER(2),
    CONSTRAINT pk_stg_products PRIMARY KEY (product_id)
);

CREATE TABLE stg_order_lines (
    line_id       NUMBER(12)   NOT NULL,
    order_id      NUMBER(12),
    product_id    NUMBER(12),
    quantity      NUMBER(8),
    unit_price    NUMBER(14,2),
    line_amount   NUMBER(14,2),
    CONSTRAINT pk_stg_order_lines PRIMARY KEY (line_id)
);

CREATE TABLE stg_categories (
    category_id   NUMBER(6)    NOT NULL,
    category_name VARCHAR2(60),
    parent_id     NUMBER(6),
    CONSTRAINT pk_stg_categories PRIMARY KEY (category_id)
);

-- Deliberately shares cust_id and status_code with stg_customer, so an unqualified
-- reference in a join is genuinely ambiguous rather than trivially resolvable.
CREATE TABLE stg_returns (
    return_id     NUMBER(12)   NOT NULL,
    order_id      NUMBER(12),
    cust_id       NUMBER(12),
    product_id    NUMBER(12),
    refund_amount NUMBER(14,2),
    status_code   NUMBER(2),
    CONSTRAINT pk_stg_returns PRIMARY KEY (return_id)
);

CREATE TABLE fct_product_sales (
    period_month  DATE,
    product_id    NUMBER(12),
    category_name VARCHAR2(60),
    gross_sales   NUMBER(14,2),
    net_sales     NUMBER(14,2),
    refund_total  NUMBER(14,2),
    units_sold    NUMBER(10),
    rank_in_month NUMBER(6),
    prior_month   NUMBER(14,2)
);

CREATE TABLE dim_customer_hier (
    cust_id       NUMBER(12)   NOT NULL,
    parent_cust_id NUMBER(12),
    depth         NUMBER(4),
    root_name     VARCHAR2(200),
    CONSTRAINT pk_dim_customer_hier PRIMARY KEY (cust_id)
);

-- Seed data.

INSERT INTO stg_categories VALUES (1, 'Hardware', NULL);
INSERT INTO stg_categories VALUES (2, 'Software', 1);
INSERT INTO stg_categories VALUES (3, 'Services', 1);

INSERT INTO stg_products VALUES (10, 'Router',  1, 250.00, 1);
INSERT INTO stg_products VALUES (11, 'Licence', 2, 900.00, 1);
INSERT INTO stg_products VALUES (12, 'Support', 3, 120.00, 0);

INSERT INTO stg_order_lines VALUES (1000, 100, 10, 2, 250.00, 500.00);
INSERT INTO stg_order_lines VALUES (1001, 100, 11, 1, 900.00, 900.00);
INSERT INTO stg_order_lines VALUES (1002, 101, 12, 4, 120.00, 480.00);
INSERT INTO stg_order_lines VALUES (1003, 102, 10, 1, 250.00, 250.00);

INSERT INTO stg_returns VALUES (500, 100, 1, 10, 250.00, 1);
INSERT INTO stg_returns VALUES (501, 102, 3, 10, 125.00, 2);

INSERT INTO dim_customer_hier VALUES (1, NULL, 0, 'ROOT-1');
INSERT INTO dim_customer_hier VALUES (2, 1, 1, 'ROOT-1');
INSERT INTO dim_customer_hier VALUES (3, 2, 2, 'ROOT-1');
INSERT INTO dim_customer_hier VALUES (4, NULL, 0, 'ROOT-4');

COMMIT;
