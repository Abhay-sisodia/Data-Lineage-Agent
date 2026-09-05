# Evidence-First Lineage Platform — context handoff

Session date: 2026-09-05. Everything of substance from the design session lives in five
published documents. Another Claude Code session can read any of them directly with the
Artifact tool: `action: "read"`, passing the URL.

---

## The five documents, in reading order

### 1 · Market, Meaning and Method 🧩

https://claude.ai/code/artifact/18dc776f-b70b-44f9-9d81-3e384772ce37

**Why this product exists and who pays for it.** The competitive landscape (platform
incumbents, catalogs, observability, migration specialists — and the real incumbent, which
is consultancies), the 10 pain points companies actually have, the two root causes beneath
them, why nobody has solved it yet, the three doors compared (migration / regulatory / M&A),
the USP and bottom-up market math.

Also holds the conceptual corrections the whole design rests on — especially
**Data + Domain + Behaviour** (code lies, data lies, behaviour doesn't), why the domain
library is a skeleton rather than an answer, generic architecture with specific packaging,
and why an IR beats transpilation.

Plus three walkthroughs: the six-step engine trace with worked intermediate outputs, the
artifact vault reproducibility example, and the cloud-services and VPC packaging reasoning.

*Read first if you're new. Skip if you're writing code this week.*

---

### 2 · Evidence-First Lineage Architecture 🔎

https://claude.ai/code/artifact/0e89e3c6-4f07-4a8d-b8a0-6c0e2139646f

**How the engine works.** Six stages end to end — collect, parse to IR, triangulate,
ledger, serve, adjudicate. Carries the evidence-tier model (A/B/C/D by evidence type, not
by model confidence), the human review loop ranked by impact × uncertainty, the query flow
with its coverage gate that returns honest unknowns, and the temporal/sync design.

Also states what the client must supply and what degrades if each input is missing, the
trust boundary, and what is deliberately out of V1.

*The reference for what you're building.*

---

### 3 · Blind Spot Register 🔦

https://claude.ai/code/artifact/ffd83dd5-c06d-4826-9fc2-71ce931cfbd2

**What it cannot see, and how that gets declared rather than hidden.** 26 blind spots
sorted into recoverable (your engineering debt), reachable (a connector or a permission),
and dark (nobody can fix — declare and count it).

Then one level down, SQL-only: 17 constructs split into **silent vs loud failures** — the
key finding being that evidence triangulation catches model error but *not* name-resolution
error, so all three witnesses can agree on the same wrong object.

Also: the differential-diagnosis loop that turns a dark-zone dead end into a ranked
shortlist with a five-minute confirming question, how to count what you were never given
(dangling references, unattributed writers, orphan upstream), why path coverage per filing
beats estate coverage, and the coverage statement template that ships in every packet.

*The silent-failure table is the one to work from on day one.*

---

### 4 · Build Plan to First Filing 🧭

https://claude.ai/code/artifact/5c11fc67-ed08-406c-8cdb-a67f36c1392e

**How it gets built.** Six phases, roughly 45 weeks, 3–5 engineers. Each phase states its
objective, what exists at the end, and the measurement that gates it — precision ≥ 95%,
30 golden questions with zero wrong, byte-identical re-run, one ruling updating N facts, a
non-engineer completing five tasks unaided, a clean install with no internet and no help.

Also carries the three configurability principles (config is data in the ledger; every limit
appears in the coverage statement; config is pinned to the run), eleven things that must be
configuration rather than code, and the six standing test assets that run on every build
forever.

*Phase 0 is your current scope. Everything else is later.*

---

### 5 · Imperative Lineage Spike 🧪

https://claude.ai/code/artifact/f8aa0b69-5245-4cc0-abc4-4e93a0e3442f

**The next three weeks, concretely.** A worked example showing why lineage through a stored
procedure needs three different mechanisms, then the construct ladder in four bands
(set-based baseline, the procedural core, statically undecidable, out of scope), the
compiler-dataflow method (CFG, def-use, interprocedural), and the dynamic-SQL log-recovery
sub-spike.

Four success metrics scored per band against hand-labelled ground truth, kill criteria
written down before the work starts, where to get nasty code without a customer, and the
v0 IR schema the spike hands you as a byproduct.

*This is your actual work order. Open it when you sit down to code.*

---

## Two orderings

- **To understand:** 1 → 2 → 3 → 4 → 5
- **To work:** 5 → 3 → 2 → 4 → 1

---

## Local files

- `v1_architecture.py` — regenerates the architecture diagram (`python v1_architecture.py`)
- `v1_architecture.png` / `.svg` — the rendered V1 architecture diagram
- Requires Graphviz on PATH (installed at `C:\Program Files\Graphviz\bin`) and `pip install diagrams`

## Full verbatim transcript

`C:\Users\abhay\.claude\projects\c--Users-abhay-Downloads-Documents\0c059af5-b371-4267-81a4-6cacf5c775ad.jsonl`

7.4 MB of raw JSONL. Complete but noisy — mostly tool traffic. The five documents above are
the distilled version; don't feed the transcript to a session, it burns context for little
gain. Resume it in place with `claude --resume` from `c:\Users\abhay\Downloads\Documents`.

---

## Where things stand

Phase 0 has not started. The immediate task is the three-week spike in document 5: determine
whether column-level lineage through real procedural PL/SQL reaches **≥95% precision** against
hand-labelled ground truth. Everything downstream is contingent on that number — if it can't
clear 95%, the regulatory positioning doesn't hold and this becomes a different company,
selling migration triage and dead-code detection to a different buyer.

Build two things only — **an analyser and a scoring harness**. No database, no API, no UI,
no Docker, no agents. The output of phase 0 is a number, not software.

The unglamorous step everyone skips: hand-label the ground truth for one package in week 1,
before you're invested in the analyser being good. Without it you have opinions instead of
a measurement.
