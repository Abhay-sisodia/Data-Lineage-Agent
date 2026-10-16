# Reviewing the lineage tool

A column-level SQL lineage analyser for **PostgreSQL**, with a lineage graph and a Claude
agent that answers questions about it. This bundle runs the whole thing in Docker: the UI,
the graph store (Neo4j) and a live PostgreSQL holding the test schemas.

## 1. Run it

**You need:** Docker Desktop (running) and Git.

```
git clone -b kg-agent https://github.com/Abhay-sisodia/Data-Lineage-Agent.git
cd Data-Lineage-Agent
cp .env.example .env            # Windows: copy .env.example .env
docker compose -f docker-compose.review.yml up --build -d
```

- The first start takes a few minutes: it builds the image, loads the test schemas into PostgreSQL and builds the lineage graph.
- Then open **http://localhost:8501**.
- **Optional:** put a Claude API key in `.env` (`ANTHROPIC_API_KEY=...`) to use the *Ask the lineage* view, then run the `up` command again. The other three views work without a key.

## 2. What to look at

The sidebar's **View** switch has four views:

| View | What it shows |
|---|---|
| **Parse SQL** | Paste SQL (or pick a test file) and press **Analyse**: the column-level edges, boundaries (where analysis stopped) and refusals (statements not analysed). |
| **Ask the lineage** | Ask a question in plain English; the answer cites graph edges, with the evidence and the agent's full trace. |
| **Lineage graph** | Pick a column and see where it comes from, or what it affects, hop by hop. |
| **Impact report** | What breaks if a column or table is dropped, renamed or retyped: affected columns, the statements and lines to edit, and the unknowns. Downloads as Markdown or CSV. |

**Five things to try:**
1. **Parse SQL**, Schema = *live PostgreSQL*, press **Pull schema**, then paste `stress/pg/final/medium/m5_names_under_load.sql` and press **Analyse**. You should get 42 edges.
2. **Lineage graph**: trace `mart.shipment_fact.gross_margin_usd` upstream.
3. **Impact report**: `s2p.po_distribution.amount_cancelled`, change *drop*: 21 affected columns, 2 statements to edit, with the exact lines.
4. **Ask** (needs a key): "Which columns feed the value of mart.fact_po_line.savings_amt?"
5. **Ask** (needs a key): "Delete all lineage for mart.fact_po_line." It must be refused.

## 3. Stop or reset

```
docker compose -f docker-compose.review.yml down        # stop, keep the data
docker compose -f docker-compose.review.yml down -v     # stop and delete everything
```

## 4. If something goes wrong

- **Port 8501 is already in use:** set `APP_PORT=8502` in `.env`, run `up` again, and open http://localhost:8502.
- **The page does not load yet:** the first start is still running. Watch it with `docker compose -f docker-compose.review.yml logs -f app`; it is ready at `UI ready`.
- **Ask says it needs a key:** add `ANTHROPIC_API_KEY` to `.env` and run `up -d` again.

More detail: [docs/kg-agent.md](docs/kg-agent.md) (how it works) and [docs/mvp-plan.md](docs/mvp-plan.md) (where it is going).
