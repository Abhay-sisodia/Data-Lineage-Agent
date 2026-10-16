# Brief: a production-grade PostgreSQL code base (20 files) for testing a lineage tool

Please create a realistic PostgreSQL 17 code base: **20 SQL files** plus the DDL they run against. It will be used to test a column-level data lineage tool. The tool reads SQL and works out, for every output column, which source columns feed it, which filter its rows, and which group or order it.

The SQL must read like code from a real data team's warehouse: real business logic, real joins and real edge cases. It must also **run without errors**, because we will execute it in PostgreSQL to check it.

---

## 1. The most important requirement: production-grade logical complexity

This is not a syntax showcase. Each file must implement **real business logic that a data team would ship and maintain**, the kind that takes a reviewer 20 minutes to understand. Specifically:

- **Logic that spans many steps.** A value is computed, renamed, adjusted, aggregated and filtered across several CTEs or tables before it lands. For example, net revenue = gross − discounts − returns, converted at an effective-dated FX rate, then recognised over a subscription period.
- **Lineage that crosses files.** A table written in one file is read by another, building chains of **4–6 hops**: `raw` → `staging` → `core` → `mart` → report.
- **Business rules that change results:** deduplication (the latest version wins), slowly changing dimensions (SCD type 2 with valid-from/valid-to), late-arriving data, soft deletes, currency conversion, allocations (cost split by a driver), cohorts, retention, inventory snapshots, revenue recognition, funnel stages.
- **Filters and conditions that matter:** status filters, effective-date joins (`BETWEEN valid_from AND valid_to`), `HAVING` on aggregates, `NOT EXISTS` exclusions, conditional aggregates (`SUM(...) FILTER (WHERE ...)`), `CASE` classifications.
- **Realistic naming and messiness:** the same column names in many tables (`id`, `status`, `amount`, `created_at`, `customer_id`), columns renamed between layers, the same alias reused in different scopes, a few mixed-case quoted identifiers, a few unqualified table names that rely on `search_path`.

**Avoid toy SQL:** `SELECT a, b FROM t`, single-table queries, or files whose only purpose is to show off one keyword.

## 2. Domain

Choose **one coherent business domain** so the files connect. For example: an online retailer with orders, payments, refunds, shipments, inventory, customers, products, promotions, finance (general ledger, FX rates) and marketing attribution.

## 3. Folder structure

```
pilot/
├── README.md                      # domain, data flow diagram (text is fine), run order
├── schema/
│   ├── 00_schemas.sql             # CREATE SCHEMA raw, staging, core, mart, ref, ...
│   ├── 01_raw.sql                 # CREATE TABLE for every source table
│   ├── 02_ref.sql                 # reference / lookup tables (FX rates, calendars, mappings)
│   └── 03_targets.sql             # tables the SQL files write into (staging, core, mart)
├── sql/
│   ├── 01_staging/                # files 01-04
│   ├── 02_core/                   # files 05-10
│   ├── 03_marts/                  # files 11-15
│   ├── 04_procedural/             # files 16-18
│   └── 05_reporting/              # files 19-20
└── expectations.md                # hand-written answers for 10 columns (see section 7)
```

- Number the files in **execution order**, e.g. `sql/02_core/07_fact_orders.sql`.
- Start each file with a header comment: its purpose, the tables it reads and writes, and a one-line complexity note.

## 4. The 20 files, what each must contain

| # | Layer | What it does | Must include |
|---|---|---|---|
| 01 | staging | Load and clean raw orders | `INSERT ... SELECT`, type casts, trimming, `DISTINCT ON` dedup by latest `updated_at` |
| 02 | staging | Upsert customers | `INSERT ... ON CONFLICT (...) DO UPDATE` with `EXCLUDED`, `COALESCE` merges |
| 03 | staging | Parse JSON events | `jsonb` operators (`->>`, `#>>`), `jsonb_array_elements`, `LATERAL` |
| 04 | staging | Payments and refunds, reconciled | `UNION ALL` of differently shaped sources, `NOT EXISTS`, `FULL OUTER JOIN` |
| 05 | core | Customer dimension, SCD type 2 | `UPDATE ... FROM` to close old rows, insert new versions, `valid_from` / `valid_to` |
| 06 | core | Product hierarchy | `WITH RECURSIVE` category tree, path arrays, cycle guard |
| 07 | core | Order fact, multi-currency | effective-dated FX join, discounts, tax, returns netted, `GROUP BY` |
| 08 | core | Inventory snapshot | window functions (`SUM() OVER (PARTITION BY ... ORDER BY ... ROWS ...)`), `LAG` / `LEAD` |
| 09 | core | Shipment SLA | timestamp arithmetic, `CASE` classifications, `LEFT JOIN LATERAL` latest-event lookup |
| 10 | core | Cleanup | `DELETE ... USING`, `TRUNCATE` + reload of a temp table, a `CREATE TEMP TABLE ... AS` used later in the file |
| 11 | marts | Daily revenue | `GROUPING SETS` / `ROLLUP`, conditional aggregates with `FILTER`, 25+ output columns |
| 12 | marts | Customer lifetime value and RFM | `NTILE`, `RANK`, recency/frequency/monetary scoring, cohort month |
| 13 | marts | Marketing attribution | first-touch / last-touch / linear weights, window functions, a correlated subquery |
| 14 | marts | Cost allocation | allocate shared costs by a driver (revenue share), `MERGE` (PostgreSQL 15+) into the mart |
| 15 | marts | Retention cohorts | cohort × month pivot with `MAX(CASE ...)` or `FILTER`, `generate_series` month spine |
| 16 | procedural | PL/pgSQL load procedure | `CREATE PROCEDURE` with variables, `IF`, a `FOR ... LOOP` over a query, `INSERT` inside the loop |
| 17 | procedural | Function with dynamic SQL | `EXECUTE format('INSERT INTO %I ...', ...)` for a partition by month, and one static path |
| 18 | procedural | Audit trigger | a trigger function writing changes to an audit table, and the `CREATE TRIGGER` |
| 19 | reporting | Executive dashboard query | **one large `SELECT` of 500+ lines: 15+ CTEs, 40+ output columns**, mixing everything above |
| 20 | reporting | Views | 3–4 `CREATE VIEW`s and 1 `CREATE MATERIALIZED VIEW` + `REFRESH`, with views built on other views |

## 5. Size and complexity

- **Total:** about **6,000–10,000 lines** of SQL across the 20 files.
- **Per file:** 100–1,000 lines.
  - At least **4 files over 400 lines.**
  - **File 19 over 500 lines.**
  - At least **6 files with several statements** (5–30 each).
- **Schema:** **40–80 tables** across 5+ schemas, with realistic column counts (8–40 per table) and types.
- **Every target column must trace to real source columns:** no constant-only outputs except the occasional flag.

## 6. Hard rules

1. **PostgreSQL 17 only.** No other SQL dialects.
2. **It must run.** On an empty PostgreSQL 17 database:
   - first run `schema/` in order, then each `sql/` file in order;
   - **no errors**, with zero rows of data being fine.
   - Please actually run it before sending.
3. **The DDL must cover every table** a file reads or writes, with:
   - primary keys and unique constraints where `ON CONFLICT` / `MERGE` need them;
   - correct column types, so date arithmetic, `jsonb` and arrays work.
4. **No psql meta-commands** (`\copy`, `\i`, `\set`) and no external extensions except `pgcrypto`, `hstore` or `citext` if needed.
5. **Synthetic only.** No real company data, names or credentials.
6. **No intentional bugs** in the SQL. If you notice a questionable business rule, keep it and mention it in the README.

## 7. `expectations.md`: the answer key

Before anyone runs the tool, write the expected lineage for **10 output columns of your choice**, spread across files 07, 11, 12, 13, 14, 19 and 20. For each one, give:

- **Value sources:** the base or raw columns whose values flow into it, and how (copied, computed, aggregated, conditional).
- **Row filters:** the columns whose conditions decide which rows reach it.
- **Grouping and ordering:** the `GROUP BY`, `PARTITION BY` or `ORDER BY` columns that shape it.
- **The path:** the tables and CTEs it passes through, in order.

Example:

```
mart.daily_revenue.net_revenue_usd
  values:   raw.order_lines.unit_price, raw.order_lines.quantity, raw.order_lines.discount_amount,
            raw.refunds.refund_amount (aggregated), ref.fx_rates.rate (derived)
  filters:  raw.orders.status, raw.orders.is_test, ref.fx_rates.valid_from/valid_to
  grouping: raw.orders.order_date, raw.orders.channel
  path:     raw.order_lines -> staging.order_lines (file 01) -> core.fact_orders (file 07)
            -> mart.daily_revenue (file 11)
```

## 8. Delivery

- One zip of the `pilot/` folder.
- **README.md** must cover:
  - the domain;
  - the run order;
  - the data flow (which file writes which table and which file reads it);
  - anything unusual you chose to include.
- Mention the PostgreSQL version you tested on.
