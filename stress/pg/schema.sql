-- =====================================================================================
-- The shared warehouse every PostgreSQL stress batch is written against.
--
-- One schema for the whole programme, so a finding in one batch can be reproduced in
-- another batch's tables. `dictionary.json` beside this file is GENERATED from it by
-- `build_dictionary.py` and is never edited by hand: if the two disagree, this file wins.
--
-- Three layers, the way a real warehouse is built:
--
--   staging   raw feeds, one table per source system
--   core      conformed dimensions and facts
--   mart      reporting tables - the targets almost every stress statement writes
--
-- NAME COLLISIONS ARE DELIBERATE. `id`, `status`, `amount`, `name`, `region`, `cost` and
-- `created_at` each appear in many tables, so an unqualified reference with two relations
-- in scope always has more than one candidate. A schema with unique column names would let
-- a resolver that guesses look exactly like one that resolves.
--
-- Written for PostgreSQL 17. Nothing here is executed by the programme; it is read.
-- =====================================================================================

CREATE SCHEMA staging;
CREATE SCHEMA core;
CREATE SCHEMA mart;

-- -------------------------------------------------------------------------------------
-- staging
-- -------------------------------------------------------------------------------------

CREATE TABLE staging.customers (
    id              bigint PRIMARY KEY,
    email           text,
    full_name       text,
    country_code    char(2),
    segment         text,
    status          text,
    signup_ts       timestamptz,
    attributes      jsonb,
    created_at      timestamptz
);

CREATE TABLE staging.orders (
    id              bigint PRIMARY KEY,
    customer_id     bigint,
    store_id        integer,
    status          text,
    order_ts        timestamptz,
    channel         text,
    currency        char(3),
    amount          numeric(12,2),
    discount_code   text,
    payload         jsonb,
    tags            text[],
    created_at      timestamptz,
    updated_at      timestamptz
);

CREATE TABLE staging.order_lines (
    id              bigint PRIMARY KEY,
    order_id        bigint,
    product_id      bigint,
    quantity        integer,
    unit_price      numeric(12,2),
    discount_pct    numeric(5,2),
    status          text,
    created_at      timestamptz
);

CREATE TABLE staging.products (
    id              bigint PRIMARY KEY,
    sku             text,
    name            text,
    category_id     integer,
    brand           text,
    list_price      numeric(12,2),
    cost            numeric(12,2),
    status          text,
    dims            jsonb,
    tags            text[],
    created_at      timestamptz
);

CREATE TABLE staging.payments (
    id              bigint PRIMARY KEY,
    order_id        bigint,
    method          text,
    amount          numeric(12,2),
    currency        char(3),
    status          text,
    paid_at         timestamptz,
    created_at      timestamptz
);

CREATE TABLE staging.shipments (
    id              bigint PRIMARY KEY,
    order_id        bigint,
    carrier_id      integer,
    warehouse_id    integer,
    status          text,
    shipped_at      timestamptz,
    delivered_at    timestamptz,
    promised_at     timestamptz,
    weight_kg       numeric(10,3),
    cost            numeric(12,2),
    tracking        jsonb,
    created_at      timestamptz
);

CREATE TABLE staging.returns (
    id              bigint PRIMARY KEY,
    order_line_id   bigint,
    reason_code     text,
    quantity        integer,
    amount          numeric(12,2),
    status          text,
    created_at      timestamptz
);

CREATE TABLE staging.inventory_snapshots (
    id              bigint PRIMARY KEY,
    warehouse_id    integer,
    product_id      bigint,
    snapshot_date   date,
    on_hand         integer,
    reserved        integer,
    created_at      timestamptz
);

CREATE TABLE staging.fx_rates (
    currency        char(3),
    rate_date       date,
    rate_to_usd     numeric(18,8),
    PRIMARY KEY (currency, rate_date)
);

CREATE TABLE staging.web_events (
    id              bigint,
    session_id      uuid,
    customer_id     bigint,
    event_type      text,
    event_ts        timestamptz,
    page            text,
    properties      jsonb
) PARTITION BY RANGE (event_ts);

-- -------------------------------------------------------------------------------------
-- core
-- -------------------------------------------------------------------------------------

CREATE TABLE core.dim_date (
    date_key        integer PRIMARY KEY,
    calendar_date   date,
    year            integer,
    quarter         integer,
    month           integer,
    week            integer,
    day_of_week     integer,
    is_weekend      boolean,
    fiscal_year     integer,
    fiscal_period   integer
);

CREATE TABLE core.dim_customer (
    customer_key        bigint PRIMARY KEY,
    customer_id         bigint,
    email               text,
    full_name           text,
    country_code        char(2),
    region              text,
    segment             text,
    status              text,
    first_order_date    date,
    valid_from          date,
    valid_to            date,
    is_current          boolean
);

CREATE TABLE core.dim_category (
    id              integer PRIMARY KEY,
    name            text,
    department      text,
    parent_id       integer
);

CREATE TABLE core.dim_product (
    product_key     bigint PRIMARY KEY,
    product_id      bigint,
    sku             text,
    name            text,
    category_id     integer,
    category_name   text,
    department      text,
    brand           text,
    list_price      numeric(12,2),
    cost            numeric(12,2),
    status          text
);

CREATE TABLE core.dim_store (
    id              integer PRIMARY KEY,
    name            text,
    region          text,
    country_code    char(2),
    opened_on       date,
    status          text
);

CREATE TABLE core.dim_carrier (
    id              integer PRIMARY KEY,
    name            text,
    mode            text,
    status          text
);

CREATE TABLE core.dim_warehouse (
    id              integer PRIMARY KEY,
    name            text,
    region          text,
    capacity        integer,
    status          text
);

CREATE TABLE core.fct_order_line (
    order_line_id   bigint PRIMARY KEY,
    order_id        bigint,
    customer_key    bigint,
    product_key     bigint,
    store_id        integer,
    order_date      date,
    channel         text,
    quantity        integer,
    unit_price      numeric(12,2),
    gross_amount    numeric(14,2),
    discount_amount numeric(14,2),
    net_amount      numeric(14,2),
    amount_usd      numeric(14,2),
    status          text
);

CREATE TABLE core.fct_shipment (
    shipment_id     bigint PRIMARY KEY,
    order_id        bigint,
    carrier_id      integer,
    warehouse_id    integer,
    shipped_date    date,
    delivered_date  date,
    promised_date   date,
    transit_days    integer,
    weight_kg       numeric(10,3),
    cost            numeric(12,2),
    status          text
);

CREATE TABLE core.fct_payment (
    payment_id      bigint PRIMARY KEY,
    order_id        bigint,
    method          text,
    amount          numeric(12,2),
    amount_usd      numeric(14,2),
    paid_date       date,
    status          text
);

-- -------------------------------------------------------------------------------------
-- mart - the targets
-- -------------------------------------------------------------------------------------

CREATE TABLE mart.sales_daily (
    sale_date       date,
    store_id        integer,
    region          text,
    orders          bigint,
    units           bigint,
    gross_amount    numeric(16,2),
    net_amount      numeric(16,2),
    avg_order_value numeric(14,2),
    rank_in_region  integer
);

CREATE TABLE mart.customer_summary (
    customer_id         bigint,
    full_name           text,
    segment             text,
    region              text,
    first_order_date    date,
    last_order_date     date,
    lifetime_orders     bigint,
    lifetime_value      numeric(16,2),
    avg_basket          numeric(14,2),
    value_rank          integer,
    value_decile        integer,
    status              text
);

CREATE TABLE mart.product_performance (
    product_id          bigint,
    category_name       text,
    period_month        date,
    units               bigint,
    revenue             numeric(16,2),
    margin              numeric(16,2),
    revenue_share       numeric(9,6),
    running_revenue     numeric(18,2),
    prev_month_revenue  numeric(16,2),
    growth_pct          numeric(9,4)
);

CREATE TABLE mart.carrier_scorecard (
    carrier_id          integer,
    carrier_name        text,
    period_month        date,
    shipments           bigint,
    on_time_pct         numeric(7,4),
    avg_transit_days    numeric(8,2),
    p50_transit_days    numeric(8,2),
    p90_transit_days    numeric(8,2),
    cost_per_kg         numeric(12,4)
);

CREATE TABLE mart.channel_mix (
    period_month        date,
    channel             text,
    region              text,
    orders              bigint,
    revenue             numeric(16,2),
    share_of_region     numeric(9,6)
);

CREATE TABLE mart.kpi_rollup (
    level               text,
    region              text,
    country_code        char(2),
    segment             text,
    orders              bigint,
    revenue             numeric(16,2),
    customers           bigint
);

CREATE TABLE mart.top_customers (
    region              text,
    customer_id         bigint,
    full_name           text,
    revenue             numeric(16,2),
    position            integer
);

-- -------------------------------------------------------------------------------------
-- Keys on mart targets (added for batch B3). `INSERT ... ON CONFLICT (k)` is only valid
-- when a unique index covers k, so an upsert test needs one to be real PostgreSQL. They
-- add no column and change no dictionary entry.
-- -------------------------------------------------------------------------------------

ALTER TABLE mart.customer_summary ADD CONSTRAINT customer_summary_pk PRIMARY KEY (customer_id);
ALTER TABLE mart.sales_daily ADD CONSTRAINT sales_daily_uk UNIQUE (sale_date, store_id);
ALTER TABLE mart.product_performance ADD CONSTRAINT product_performance_uk UNIQUE (product_id, period_month);
