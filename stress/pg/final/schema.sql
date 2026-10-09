-- The final PostgreSQL acceptance suite's schema (2026-10-07). Synthetic, PostgreSQL 17.
--
-- Shaped on the three production samples the suite draws on: shipments and logistics
-- (lgx), purchasing and requisitions (s2p), customers and organisations (crm), entity
-- events (evt), conformed dimensions (gold), and load targets (mart).
--
-- Column names repeat on purpose (`status`, `amount`, `created_at`, `integration_id`,
-- `last_modification_date_time`, `customer_id`, `region_code`), so every unqualified
-- reference in the tests is a real resolution question.
--
-- dictionary.json is generated from this file and never edited:
--     .\.venv\Scripts\python.exe stress/pg/build_dictionary.py --schema stress/pg/final/schema.sql --out stress/pg/final/dictionary.json --default-schema public
-- There is no `public` table, so an unqualified name that is not a CTE resolves to nothing.

-- ======================================================================= lgx: logistics
CREATE TABLE lgx.shipment_header (
    shipment_id                 bigint,
    source_system               text,
    shipment_number             text,
    customer_id                 bigint,
    ship_from_org_id            bigint,
    ship_to_site_id             bigint,
    carrier_code                text,
    incoterm_code               text,
    currency_code               char(3),
    status_code                 text,
    header_attrs                jsonb,
    version_no                  integer,
    is_deleted                  boolean,
    created_at                  timestamptz,
    updated_at                  timestamptz
);

CREATE TABLE lgx.shipment_line (
    shipment_line_id            bigint,
    shipment_id                 bigint,
    source_system               text,
    item_id                     bigint,
    line_type_code              text,
    line_status                 text,
    ordered_qty                 numeric(18,4),
    shipped_qty                 numeric(18,4),
    cancelled_qty               numeric(18,4),
    unit_selling_price          numeric(18,6),
    ship_date                   date,
    promise_date                date,
    request_date                date,
    line_attrs                  jsonb,
    etl_batch_id                bigint,
    last_update_ts              timestamptz
);

CREATE TABLE lgx.item_master (
    item_id                     bigint,
    item_code                   text,
    item_desc                   text,
    item_class                  text,
    uom_code                    text,
    std_cost                    numeric(18,6),
    list_price                  numeric(18,6),
    weight_kg                   numeric(12,3),
    attrs                       jsonb,
    tags                        text[],
    created_at                  timestamptz
);

CREATE TABLE lgx.bill_of_material (
    parent_item_id              bigint,
    component_item_id           bigint,
    qty_per                     numeric(18,6),
    scrap_pct                   numeric(6,3),
    bom_status                  text,
    effective_from              date,
    effective_to                date
);

CREATE TABLE lgx.carrier (
    carrier_code                text,
    carrier_name                text,
    mode_code                   text,
    active_flag                 char(1),
    end_date                    date
);

CREATE TABLE lgx.carrier_service (
    carrier_code                text,
    service_level               text,
    transit_days                integer,
    mode_code                   text
);

CREATE TABLE lgx.transport_mode (
    mode_code                   text,
    mode_desc                   text
);

CREATE TABLE lgx.warehouse (
    warehouse_id                bigint,
    warehouse_code              text,
    org_id                      bigint,
    region_code                 text,
    latitude                    numeric(9,6),
    longitude                   numeric(9,6)
);

CREATE TABLE lgx.package_event (
    event_id                    bigint,
    shipment_id                 bigint,
    event_type                  text,
    event_ts                    timestamptz,
    location_code               text,
    payload                     jsonb
);

CREATE TABLE lgx.edi_document (
    edi_doc_id                  bigint,
    doc_status                  text,
    payload                     xml,
    received_at                 timestamptz
);

-- ================================================================== s2p: procure-to-pay
CREATE TABLE s2p.po_header (
    po_header_id                bigint,
    segment1                    text,
    type_lookup_code            text,
    document_status             text,
    vendor_id                   bigint,
    vendor_site_id              bigint,
    agent_id                    bigint,
    currency_code               char(3),
    rate                        numeric(18,8),
    approved_flag               char(1),
    approved_date               timestamptz,
    closed_date                 timestamptz,
    style_id                    bigint,
    terms_id                    bigint,
    fob_lookup_code             text,
    created_at                  timestamptz,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.po_header_ext (
    po_header_id                bigint,
    attribute1                  text,
    attribute4                  text,
    attribute9                  text,
    attribute12                 text,
    attribute_number1           numeric
);

CREATE TABLE s2p.po_line (
    po_line_id                  bigint,
    po_header_id                bigint,
    line_number                 numeric(18,4),
    item_id                     bigint,
    category_id                 bigint,
    item_description            text,
    uom_code                    text,
    unit_price                  numeric(18,6),
    quantity                    numeric(18,4),
    amount                      numeric(18,4),
    list_price_per_unit         numeric(18,6),
    market_price                numeric(18,6),
    from_header_id              bigint,
    from_line_id                bigint,
    contract_id                 bigint,
    line_status                 text,
    purchase_basis              text,
    matching_basis              text,
    line_type_id                bigint,
    created_at                  timestamptz,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.po_line_ext (
    po_line_id                  bigint,
    attribute1                  text,
    attribute2                  text,
    attribute4                  text,
    attribute12                 text,
    attribute13                 text,
    attribute19                 text
);

CREATE TABLE s2p.po_line_location (
    line_location_id            bigint,
    po_line_id                  bigint,
    shipment_num                integer,
    quantity                    numeric(18,4),
    price_override              numeric(18,6),
    need_by_date                timestamptz,
    promised_date               timestamptz,
    ship_to_location_id         bigint,
    receipt_required_flag       char(1),
    inspection_required_flag    char(1),
    match_option                char(1),
    schedule_status             text,
    closed_reason               text,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.po_distribution (
    distribution_id             bigint,
    line_location_id            bigint,
    distribution_num            integer,
    quantity_ordered            numeric(18,4),
    quantity_delivered          numeric(18,4),
    quantity_billed             numeric(18,4),
    quantity_cancelled          numeric(18,4),
    amount_ordered              numeric(18,4),
    amount_delivered            numeric(18,4),
    amount_billed               numeric(18,4),
    amount_cancelled            numeric(18,4),
    recoverable_tax             numeric(18,4),
    nonrecoverable_tax          numeric(18,4),
    code_combination_id         bigint,
    set_of_books_id             bigint,
    req_distribution_id         bigint,
    deliver_to_person_id        bigint,
    created_at                  timestamptz,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.po_version (
    po_header_id                bigint,
    revision_num                integer,
    submitted_date              timestamptz,
    document_date               timestamptz,
    processed_date              timestamptz,
    accepted_date               timestamptz,
    approved_date               timestamptz,
    change_order_description    text
);

CREATE TABLE s2p.req_header (
    req_header_id               bigint,
    requisition_number          text,
    preparer_id                 bigint,
    interface_source_code       text,
    approved_date               timestamptz,
    justification               text,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.req_line (
    req_line_id                 bigint,
    req_header_id               bigint,
    line_number                 numeric(18,4),
    unit_price                  numeric(18,6),
    quantity                    numeric(18,4),
    need_by_date                timestamptz,
    unspsc_code                 text,
    last_approval_date          timestamptz,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.req_distribution (
    req_distribution_id         bigint,
    req_line_id                 bigint,
    distribution_num            integer,
    distribution_quantity       numeric(18,4),
    code_combination_id         bigint,
    last_modification_date_time timestamptz
);

CREATE TABLE s2p.item_category (
    category_id                 bigint,
    category_name               text,
    parent_category_id          bigint
);

CREATE TABLE s2p.uom (
    uom_code                    text,
    uom_name                    text
);

CREATE TABLE s2p.ledger (
    ledger_id                   bigint,
    currency_code               char(3),
    ledger_type_code            char(1)
);

CREATE TABLE s2p.supplier (
    vendor_id                   bigint,
    vendor_name                 text,
    vendor_type                 text,
    parent_vendor_id            bigint,
    status                      text
);

CREATE TABLE s2p.supplier_site (
    vendor_site_id              bigint,
    vendor_id                   bigint,
    vendor_site_code            text,
    country_code                char(2)
);

CREATE TABLE s2p.person (
    person_id                   bigint,
    username                    text,
    full_name                   text,
    active_flag                 char(1)
);

-- ========================================================== crm: customers and organisation
CREATE TABLE crm.customer (
    customer_id                 bigint,
    customer_name               text,
    customer_class              text,
    parent_customer_id          bigint,
    country_code                char(2),
    credit_limit                numeric(18,2),
    segment                     text,
    status                      text,
    valid_from                  date,
    valid_to                    date,
    created_at                  timestamptz
);

CREATE TABLE crm.customer_site (
    site_id                     bigint,
    site_code                   text,
    customer_id                 bigint,
    country_code                char(2),
    latitude                    numeric(9,6),
    longitude                   numeric(9,6),
    time_zone_name              text,
    address_lines               text[]
);

CREATE TABLE crm.organization (
    org_id                      bigint,
    parent_org_id               bigint,
    org_name                    text,
    org_type                    text,
    is_active                   boolean
);

CREATE TABLE crm.contact (
    contact_id                  bigint,
    customer_id                 bigint,
    email                       text,
    role                        text,
    is_primary                  boolean,
    created_at                  timestamptz
);

-- ============================================================== evt: entity event pipeline
CREATE TABLE evt.entity_event (
    event_id                    bigint,
    entity_id                   bigint,
    parent_id                   bigint,
    status                      text,
    payload                     jsonb,
    display_name                text,
    amount                      numeric(18,2),
    created_at                  timestamptz,
    ingested_at                 timestamptz
);

CREATE TABLE evt.activity (
    activity_id                 bigint,
    entity_id                   bigint,
    kind                        text,
    amount                      numeric(18,2),
    label                       text,
    note                        text,
    region_code                 text,
    sequence_no                 integer,
    created_at                  timestamptz
);

CREATE TABLE evt.metric (
    entity_id                   bigint,
    region_code                 text,
    category_code               text,
    score                       numeric(6,4),
    metric                      numeric(18,4),
    event_date                  date
);

CREATE TABLE evt.relationship (
    child_id                    bigint,
    parent_id                   bigint,
    edge_type                   text,
    weight                      numeric(8,4)
);

CREATE TABLE evt.flag (
    entity_id                   bigint,
    flag_code                   text,
    active_at                   timestamptz,
    active_flag                 boolean,
    reason_code                 text
);

CREATE TABLE evt.audit_log (
    audit_id                    bigint,
    entity_type                 text,
    entity_id                   bigint,
    audit_ts                    timestamptz,
    audit_user                  text,
    audit_action                text
);

-- =================================================================== gold: dimensions
CREATE TABLE gold.dim_status (
    row_wid                     bigint,
    integration_id              text,
    status_code                 text,
    status_name                 text,
    status_category             text,
    datasource_num_id           integer
);

CREATE TABLE gold.dim_organization (
    row_wid                     bigint,
    integration_id              text,
    org_name                    text,
    organization_start_date     date,
    organization_end_date       date,
    datasource_num_id           integer
);

CREATE TABLE gold.dim_date (
    date_wid                    integer,
    calendar_date               date,
    fiscal_year                 integer,
    fiscal_period_name          text,
    is_holiday                  boolean
);

CREATE TABLE gold.dim_employee (
    employee_sk                 bigint,
    integration_id              text,
    full_name                   text,
    effective_from_date         timestamptz,
    effective_to_date           timestamptz,
    is_delete                   char(1),
    datasource_num_id           integer
);

CREATE TABLE gold.dim_supplier (
    vendor_key                  bigint,
    integration_id              text,
    supplier_name               text,
    datasource_num_id           integer
);

CREATE TABLE gold.fx_rate (
    from_ccy                    char(3),
    to_ccy                      char(3),
    rate_date                   date,
    rate_type                   text,
    conversion_rate             numeric(18,8),
    loaded_at                   timestamptz
);

CREATE TABLE gold.dim_currency (
    row_wid                     bigint,
    currency_code               char(3),
    currency_name               text
);

-- ======================================================================= mart: targets
CREATE TABLE mart.fact_po_line (
    integration_id              text UNIQUE,  -- h2 S1 upserts ON CONFLICT (integration_id)
    po_header_id                bigint,
    po_number                   text,
    line_number                 integer,
    supplier_id                 bigint,
    buyer_id                    bigint,
    item_description            text,
    line_amt                    numeric(18,4),
    po_dist_qty                 numeric(18,4),
    should_cost                 numeric(18,4),
    savings_amt                 numeric(18,4),
    x_po_match                  text,
    overdue_days                integer,
    overdue_bucket              text,
    status_code                 text,
    load_ts                     timestamptz
);

CREATE TABLE mart.supplier_spend (
    supplier_id                 bigint,
    supplier_name               text,
    spend_year                  integer,
    po_count                    bigint,
    total_spend                 numeric(18,4),
    total_savings               numeric(18,4),
    spend_rank                  integer
);

CREATE TABLE mart.shipment_fact (
    shipment_line_id            bigint,
    shipment_id                 bigint,
    customer_id                 bigint,
    item_id                     bigint,
    ship_date                   date,
    shipped_qty                 numeric(18,4),
    ext_amount_usd              numeric(18,4),
    gross_margin_usd            numeric(18,4),
    delivery_class              text,
    is_late                     boolean,
    load_ts                     timestamptz
);

CREATE TABLE mart.customer_snapshot (
    customer_id                 bigint,
    customer_name               text,
    segment                     text,
    lifetime_revenue            numeric(18,4),
    last_ship_date              date,
    open_lines                  bigint,
    status                      text,
    updated_at                  timestamptz
);

CREATE TABLE mart.entity_state (
    entity_id                   bigint,
    status                      text,
    amount                      numeric(18,2),
    activity_total              numeric(18,2),
    flag_count                  bigint,
    last_seen                   timestamptz
);

CREATE TABLE mart.load_audit (
    run_id                      bigint,
    table_name                  text,
    rows_affected               bigint,
    run_ts                      timestamptz
);
