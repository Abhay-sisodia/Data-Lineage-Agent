# Academic literature: data-flow, provenance and column-level lineage of procedural SQL (PL/pgSQL, PL/SQL, T-SQL, SQL/PSM) and database programs

Scope note: these notes are for the "routine semantics" layer of a static, LLM-free PostgreSQL 17 column-lineage analyser. "Implements/evaluates" vs "proposes" is marked for each item. Where a detail came only from a search-engine snippet rather than the primary text, it is flagged "(snippet)". Researched 2026-10-16.

---

## Q1. Static data-flow / def-use / program slicing of stored procedures and database application programs

### Takeaway
There is a small, older software-engineering literature on slicing and dataflow for database programs (Willmor/Embury/Shao 2004, Maule et al. 2008, Dasgupta et al. 2009, Halder & Cortesi 2010s). It extends the program dependence graph with dependencies that go through database state. Almost all of it targets host-language programs (Java, C#) that embed SQL, not PL/pgSQL bodies. The only published system that does column-level lineage over warehouse stored procedures at scale (Tomingas et al. 2016, dLineage) is rule-based and reports only performance, not accuracy. I found no peer-reviewed paper that does sound def-use or column lineage specifically over PL/pgSQL routines.

### Cited Findings
- **Willmor, Embury, Shao: "Program slicing in the presence of database state", ICSM 2004 (IEEE), pp. 448-452.** This paper implements and proposes slicing over both program state and database state. It adds two new forms of data dependency to the standard program dependence graph. In the paper these are dependencies from program to database and from database to database through SQL statements. — [Cardiff ORCA record](https://orca.cardiff.ac.uk/id/eprint/45517/). Artifacts: none found.
- **Maule, Emmerich, Rosenblum: "Impact analysis of database schema changes", ICSE 2008.** Static dataflow analysis extracts every database interaction an OO application may make, and the result predicts the impact of schema changes. Program slicing shrinks the analysed program because precision is expensive. Evaluated on a case study: under 2 minutes, "no false negatives and a low level of false positives". — [academia.edu copy](https://www.academia.edu/60290609/Impact_analysis_of_database_schema_changes); [Leipzig schema-evolution bibliography](https://pubs.dbs.uni-leipzig.de/se/node/974). Artifacts: none found.
- **Dasgupta, Narasayya, Syamala: "A Static Analysis Framework for Database Applications", ICDE 2009 (Microsoft Research).** This is a static analysis framework for ADO.NET applications. It adapts traditional program analysis by understanding data-access APIs. Use cases: SQL injection detection, workload extraction, performance problems, and checking data-integrity constraints. Program slicing is used to make dataflow analysis scale (snippet). — [MSR publication page](https://www.microsoft.com/en-us/research/publication/a-static-analysis-framework-for-database-applications/); [PDF](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/StaticAnalysis.pdf); patent [US8452754B2](https://patents.google.com/patent/US8452754). Artifacts: none public (implemented inside Microsoft tooling).
- **Halder & Cortesi (Univ. Venice): abstract interpretation and semantics-based abstract program slicing of database query languages** (several papers, 2010s; Halder PhD thesis). The work gives a denotational semantics, concrete and abstract, for data-intensive programs that embed SELECT/UPDATE/INSERT/DELETE. It defines semantic data dependences and conditional dependences for programs with embedded SQL. These refine syntax-based "Database-Oriented Program Dependence Graphs" (DOPDG) into a more precise semantics-based abstract slicer. The work was extended to Hibernate HQL. Mostly formal; implementation status unclear. — [Halder thesis (IRIS Unive)](https://iris.unive.it/retrieve/34227e8d-14b2-428b-9d88-ede5a5685deb/thesisHalder.pdf); [Abstract program slicing of database query languages](https://www.academia.edu/68051247/Abstract_program_slicing_of_database_query_languages); [IFIP 2015 paper record](https://dl.ifip.org/IFIP-LNCS-9339/hal-01444471).
- **Tomingas, Järv, Tammet: "Discovering Data Lineage from Data Warehouse Procedures", KDIR 2016 (IC3K), pp. 101-110.** The system computes component dependencies and column lineage from schema plus "a large set of associated procedures and queries, independently of actual data". Its pipeline, read from the PDF:
  - scanners collect the inputs;
  - a SQL parser uses customised grammars (GoldParser engine plus the Java-based XDTL engine);
  - a rule-based parse-tree mapper extracts expressions;
  - a query resolver "expand[s] and resolve[s] all the variables, aliases, sub-query expressions";
  - an expression weight calculator assigns each edge a column transformation weight Wt and a column filter weight Wf on [0,1] (Wf covers columns in JOIN/WHERE);
  - a probabilistic rule-based reasoning engine propagates the weights, with the transitive-closure rules written as SQL in PostgreSQL;
  - the tool is "dLineage".

  Six real datasets were used; DS4 and DS5 are Oracle warehouses whose transformations live in stored procedures. **The evaluation reports processing time and graph size only, with no precision or recall.** — [SciTePress page](https://www.scitepress.org/Papers/2016/60543); [PDF](https://www.scitepress.org/PublishedPapers/2016/60543/60543.pdf). Artifacts: commercial (dlineage.com); no open code found.
- **Habringer, Moser, Pichler: "Reverse Engineering PL/SQL Legacy Code: An Experience Report", 2014.** Tool support turns PL/SQL in the steel-making domain into a more abstract representation for stakeholders. It combines general-purpose analysis techniques with domain-specific representations. Industrial experience report, not a lineage evaluation. — [FH OÖ Pure record](https://pure.fh-ooe.at/en/publications/reverse-engineering-plsql-legacy-code-an-experience-report/).
- **"Automated procedure clustering for reverse engineering PL/SQL programs"** (Özyeğin University) is a related PL/SQL reverse-engineering paper; I did not read it. — [Özyeğin repository](https://eresearch.ozyegin.edu.tr/handle/10679/4343).
- **"PL/SQL Advisor: a Static Analysis-based Tool to Suggest Improvements" (SBSI, Brazil).** It represents a stored procedure as a Control Dependence Tree derived from the CFG and detects improvement opportunities. It is a code-quality tool, not a lineage tool. — [SBC PDF](https://sol.sbc.org.br/index.php/sbsi/article/download/5702/5599/).
- **Gupta & Ramachandra (2021, see Q6)** did extract 38 features by static analysis from thousands of real T-SQL procedures, UDFs and triggers. To do so they parsed the objects, derived types, built ASTs and CFGs, and captured inter-object dependencies. This is evidence that a CFG-level IR over real procedural code is practical at scale, though the analysis is not lineage. — [PVLDB 14(8) PDF](https://www.vldb.org/pvldb/vol14/p1378-ramachandra.pdf).
- **Campbell, Arab, Glavic: "Efficient Answering of Historical What-if Queries" (Mahif), SIGMOD 2022.** It uses **program slicing and data slicing over transactional histories** (sequences of UPDATE/INSERT/DELETE) to decide which updates can be excluded from reenactment. This is a rare modern DB-venue use of slicing over SQL DML sequences. — [arXiv 2203.12860](https://arxiv.org/abs/2203.12860v1); [IIT project page](https://www.cs.uic.edu/~bglavic/dbgroup/projects/updateprov.html).

### Inferences
- The Willmor/Halder line gives the right dependence vocabulary for our IR. Treat each table column as a global "variable": DML statements define it, and queries and later statements use it. Then trigger and procedure effects become ordinary def-use edges, and an "effects" summary per routine is just the set of DB-column defs and uses.
- Tomingas's Wt/Wf split matches our existing value-versus-filter flow distinction. Their "query resolver" (resolve variables and aliases before lineage) is essentially constant and copy propagation. Nobody has published accuracy numbers for this approach, so it is not evidence of correctness.
- Mahif shows that slicing over a straight-line DML sequence is cheap and useful. It is a close template for routine bodies once control flow is lowered.

### Gaps
- I found no paper on def-use or slicing specifically for PL/pgSQL. Search terms "PL/pgSQL static analysis" and "plpgsql dataflow" returned only tooling (plpgsql_check), not research. A follow-up on Google Scholar or DBLP is recommended.
- I could not verify the venue of Habringer et al. (very likely ICSME 2014, not confirmed) or the authors and venue of "PL/SQL Advisor".
- Tan and colleagues' work on PL/SQL or 4GL dataflow (1990s-2000s) was not searched.

---

## Q2. Provenance for procedural / imperative SQL. Which systems give column-level (where- or attribute-level) results?

### Takeaway
Mature provenance systems are **dynamic**: they rewrite queries or replay execution over actual data. They cover single queries (Perm, ProvSQL, Müller/Grust), and DML sequences and transactions through reenactment (GProM). Attribute-level where-provenance exists in Perm (COPY semantics), ProvSQL (a "where mode") and Müller/Grust (cell-level where/why, including recursive CTEs and recursive UDFs). I found no system that computes provenance for arbitrary PL/pgSQL routines with loops, exceptions and dynamic SQL. The 2023 ProvPg paper claims procedural-language support but I could not verify its details. These systems are best used as **test oracles** for a static analyser, not as the analyser itself.

### Cited Findings
- **Perm (Glavic & Alonso), ICDE 2009, "Perm: Processing Provenance and Data on the same Data Model through Query Rewriting".** Implemented as a PostgreSQL extension. Query rewriting annotates tuples with provenance. It supports why-provenance (INFLUENCE) and several kinds of **where-provenance (COPY)** through an `ON CONTRIBUTION` modifier, so the provenance is at attribute level. Sources: [Perm overview](https://permdbms.sourceforge.net/); [ICDE 2009 PDF](https://permdbms.sourceforge.net/pdfs/GA09.pdf); [UIC bib entry](https://www.cs.uic.edu/~bglavic/dbgroup/bibliography/GA09.html). One secondary description says Perm handles "SPJUA operations, nested queries and user-defined functions" (snippet, unverified in the primary source). It is old PostgreSQL 8.x-era code on SourceForge.
- **GProM (Glavic group, IIT).** Generic provenance middleware based on rewriting an algebraic graph representation. It covers queries, updates, transactions and operations spanning several transactions. **Reenactment** is declarative replay of DML from audit logs plus time travel. It supports serializable transactions and read-committed snapshot isolation. It has a Datalog front-end with why and why-not provenance graphs, and Oracle is the main backend. **License Apache 2.0**, about 2,940 commits, active CI. — [GitHub IITDBGroup/gprom](https://github.com/IITDBGroup/gprom); [project page](https://www.cs.uic.edu/~bglavic/dbgroup/projects/gprom.html); [TaPP 2014 paper](https://usenix.org/conference/tapp2014/agenda/presentation/arab).
  - Arab, Gawlick, Radhakrishnan, Guo, Glavic: "A Generic Provenance Middleware for Database Queries, Updates, and Transactions", TaPP 2014.
  - Arab et al.: "Reenactment for Read-Committed Snapshot Isolation", CIKM 2016.
  - Arab et al.: "Using Reenactment to Retroactively Capture Provenance for Transactions", IEEE TKDE 2018 (DOI 10.1109/TKDE.2017.2769056).
  - Arab: PhD thesis "Provenance for Transactional Updates", IIT 2019.

  All four are listed at [IIT update-provenance project page](https://www.cs.uic.edu/~bglavic/dbgroup/projects/updateprov.html). That page does not document PL/SQL, stored-procedure or MERGE support.
- **ProvSQL (Senellart et al.).** PostgreSQL extension (PostgreSQL ≥ 11). It supports m-semirings and the δ operator, provenance circuits, probability and Shapley computation, and HAVING. "ProvSQL Studio" has a **Where mode** that highlights the source cells of each output value. **MIT license.** Formal foundations are machine-checked in Lean 4. — [GitHub PierreSenellart/provsql](https://github.com/PierreSenellart/provsql); [TaPP 2018 talk](https://www.usenix.org/conference/tapp2018/presentation/senellart). The README excerpt did not mention support for DML or PL/pgSQL functions.
- **Müller, Dietrich, Grust (Tübingen).**
  - "You Say 'What', I Hear 'Where' and 'Why' — (Mis-)Interpreting SQL to Derive Fine-Grained Provenance", PVLDB 2018. Cell-level where- and why-provenance for "very rich SQL dialects (including recursion, windowed aggregates, and user-defined functions)". It works by a compositional source-level rewrite that turns "an input SQL query … into its own interpreter", replacing values with dependency sets. — [arXiv 1805.11517](https://arxiv.org/abs/1805.11517).
  - Earlier version: "Provenance for SQL Based on Abstract Interpretation: Value-less, but Worthwhile", PVLDB 2015.
  - "Data Provenance for Recursive SQL Queries", TaPP 2022 (DOI 10.1145/3530800.3534536). Extends where/why provenance to **recursive CTEs and recursive UDFs** via language-level rewriting and two-phase evaluation. — [Tübingen page](https://db.inf.uni-tuebingen.de/publications/2022/data-provenance-for-recursive-sql-queries/).
  - Müller PhD thesis "Detached Provenance Analysis", 2020. — [Tübingen publications list](https://db.cs.uni-tuebingen.de/publications/).
- **Tang, Zhao, Lin, Zhang, Zhang: "Modeling the Data Provenance of Relational Databases Supporting Full-Featured SQL and Procedural Languages", Applied Sciences 13(1):64, Dec 2022 / 2023 (MDPI).** Proposes provenance graphs and "provenance routes" at several granularities (table, field, tuple, value), with an explicit claim to support procedural languages. Prototype **ProvPg** is built on PostgreSQL. — [MDPI](https://www.mdpi.com/2076-3417/13/1/64); [DOAJ](https://doaj.org/article/19874133dd07457f9ee180183473fdab). The full text was blocked (HTTP 403), so I could not check how PL/pgSQL statements are modelled or whether code is public.
- **"Data Provenance Support in Relational Databases for Stored Procedures" (Springer LNCS chapter, 2009, DOI 10.1007/978-3-642-04205-8_10).** Describes "PSP", which uses SQL:2003 XML capabilities to track the lineage of data processed by any stored procedure (snippet). — [Springer](https://link.springer.com/chapter/10.1007/978-3-642-04205-8_10). Authors and evaluation not verified (paywalled).
- **Lipstick: Amsterdamer, Davidson, Deutch, Milo, Stoyanovich, Tannen, "Putting Lipstick on Pig: Enabling Database-style Workflow Provenance", PVLDB 5(4):346-357, 2011.** Exposes module internals as Pig Latin to get fine-grained (semiring-style) provenance inside workflows. It uses a compact provenance graph with ZoomIn/ZoomOut and comes with a benchmark. — [PVLDB PDF](https://vldb.org/pvldb/vol5/p346_yaelamsterdamer_vldb2012.pdf); [arXiv 1201.0231](https://arxiv.org/abs/1201.0231). The relevant idea is to model an opaque module by writing its behaviour in a declarative dataflow language and then reusing database provenance. That is the same move as "lower each routine to a canonical relational IR".

### Inferences
- Cell-level where-provenance from Perm, ProvSQL and Müller/Grust is a **dynamic** ground truth. A practical use is to run small PL/pgSQL test routines whose effects are re-expressed as plain SQL or DML and check the static column edges against the observed cell provenance. GProM reenactment is the only open (Apache-2.0) system that handles **DML sequences**, so it is the most relevant oracle for procedure and trigger effects. It is Oracle-centric, though.
- Müller/Grust's "a query becomes its own interpreter, values replaced by dependency sets" is conceptually the abstract interpretation we want, done statically over a routine IR. The abstract value of every variable or column is the set of source columns it depends on, split into value and filter dependencies, and the join over loops is set union, which reaches a fixpoint quickly.
- None of these systems handles dynamic SQL, exceptions or arbitrary PL/pgSQL control flow. This matches the compile-to-SQL limits in Q3.

### Gaps
- I did not verify whether ProvSQL or Perm propagate provenance through PL/pgSQL or SQL-language function calls, or through INSERT…SELECT.
- noWorkflow (Python script provenance) and other "provenance for imperative programs" work were not verified in this session. They are mentioned in the brief but I have no sources here.
- The ProvPg (Tang et al.) details and the PSP authors are unverified because of paywalls and 403 errors.

---

## Q3. IRs and formal semantics for procedural SQL, and lowering routines to relational algebra or recursive CTEs (could inlining make single-statement lineage enough?)

### Takeaway
The strongest relevant work is Microsoft's Froid/Aggify/UDF-decorrelation line and Tübingen's PL/SQL→SQL compiler (Duta/Hirn/Grust). Tübingen's compiler lowers PL/pgSQL to **SSA (all control flow becomes `goto`), then ANF (mutually tail-recursive functions), then one SQL UDF, then WITH RECURSIVE**, while keeping embedded SQL expressions and queries as black boxes. **Both lines explicitly exclude DML (INSERT/UPDATE/DELETE), DDL, exception handlers and dynamic SQL**, and Froid also excludes loops and cursors. Gupta & Ramachandra (2021) report that DML and exception handling are "fairly common in stored procedures". So whole-routine inlining into a single SQL statement cannot be the general mechanism for procedures or triggers. The *intermediate forms* (SSA/ANF with SQL expressions as leaves) are exactly the small canonical IR we need.

### Cited Findings
**Froid (Microsoft)**
- **Ramachandra, Park, Emani, Halverson, Galindo-Legaria, Cunningham: "Froid: Optimization of Imperative Programs in a Relational Database", PVLDB 11(4), 2017.** Implements the conversion of an entire scalar T-SQL UDF into one relational expression that is substituted into the calling query "like view substitution". The UDF body is split into regions (sequential, conditional, loop). Each statement becomes a relational expression, and the statements are chained through **derived tables and the Apply operator**, with variables as columns. — [arXiv 1712.00498](https://arxiv.org/pdf/1712.00498); [MSR page](https://www.microsoft.com/en-us/research/publication/froid-optimization-of-imperative-programs-in-a-relational-database/).
  - Supported constructs, quoted from §3.4: "DECLARE, SET … SELECT: SQL query with multiple variable assignments … IF/ELSE: Branching with arbitrary levels of nesting … RETURN: Single or multiple return statements … UDF: Nested/recursive function calls … Others: … EXISTS, ISNULL".
  - Loops: "we have prototyped algebrization of cursor loops … scalar UDFs with loops are quite rare … Therefore, we have currently disabled support for loops."
  - Coverage: of 85,329 scalar UDFs in the top-100 Azure SQL DB customer databases, Froid handled **51,047 (59.8%)**; "The UDFs that could not be transformed contained constructs not supported by Froid."
  - In product: SQL Server 2019 "Scalar UDF Inlining". Implemented in about 1.5k LOC in SQL Server 2017. [MSR Froid page](https://www.microsoft.com/en-us/research/publication/froid-optimization-of-imperative-programs-in-a-relational-database/); [MSR blog](https://www.microsoft.com/en-us/research/blog/optimizing-imperative-functions-in-relational-databases-with-froid/).
  - Artifacts: none (closed source).

**Related Microsoft / IIT Bombay work**
- **Gupta, Purandare, Ramachandra: "Aggify: Lifting the Curse of Cursor Loops using Custom Aggregates", SIGMOD 2020.** Implements rewriting of a cursor loop over a query into a generated custom aggregate that is semantically equivalent to the loop, which removes the loop. — [MSR PDF](https://www.microsoft.com/en-us/research/uploads/prod/2020/08/Aggify-SIGMOD2020.pdf); [arXiv 2004.05378](https://arxiv.org/pdf/2004.05378v2).
- **Simhadri, Ramachandra, Chaitanya, Guravannavar, Sudarshan: "Decorrelation of user defined function invocations in queries", ICDE 2014.** Models imperative UDF execution with the Apply construct so transformation rules can decorrelate or inline UDF bodies. Evaluated on two commercial DBMSs. — [IAS repository](https://repository.ias.ac.in/128469); patent [US10191943B2](https://patents.google.com/patent/US10191943).
- **Emani, Ramachandra, Bhattacharya, Sudarshan: "Extracting Equivalent SQL from Imperative Code in Database Applications", SIGMOD 2016 (DBridge).** Extracts a concise algebraic representation of imperative code plus queries (Java/JDBC/Hibernate) and translates it to SQL. — [MSR page](https://www.microsoft.com/en-us/research/publication/extracting-equivalent-sql-from-imperative-code-in-database-applications/); [DBridge](https://www.microsoft.com/en-us/research/publication/dbridge-translating-imperative-code-to-sql/).

**Tübingen PL/SQL→SQL compiler (Grust group; targets PostgreSQL PL/pgSQL)**
- **Duta, Hirn, Grust: "Compiling PL/SQL Away", CIDR 2020.** Read from the PDF. Pipeline: "SSA: Turn PL/SQL function f into static single assignment (SSA) form. This maps the diversity of PL/SQL control flow constructs to the single goto primitive. ANF: … expresses iteration in terms of (mutually tail-)recursive functions. UDF: Flatten mutual recursion … into one tail-recursive SQL UDF. SQL: … embed the body into a template query based on WITH RECURSIVE."
  - In the SSA programs, "expressions … are regular SQL expressions", and embedded queries are parameterised by SSA variables.
  - Following Froid, `let…in` is compiled to chains of `LEFT JOIN LATERAL … ON true`.
  - Evaluated on PostgreSQL 11.3, with a WITH ITERATE prototype added to PostgreSQL.
  - Limitations, quoted: exceptions are "more of a challenge … detection of exception conditions from within a SQL query appears difficult"; cursors: "We are currently underway to devise a compilation scheme"; "Dynamic SQL (PL/SQL's string-based EXECUTE) will probably never compile to SQL"; "A generalization to set-returning functions (the RETURN NEXT of PL/SQL) has already been found to integrate elegantly"; row/array variables are fine if the RDBMS can store them in cells.
  - Sources: [CIDR PDF](https://www.vldb.org/cidrdb/papers/2020/p1-duta-cidr20.pdf); [Tübingen page](https://db.inf.uni-tuebingen.de/publications/2020/compiling-pl/sql-away/).
- **Hirn & Grust: "PL/SQL Without the PL", SIGMOD 2020 (demo).** Interactive source-to-source compiler with visualisation of intermediate forms. — [Tübingen page](https://db.inf.uni-tuebingen.de/publications/2020/pl/sql-without-the-pl/).
- **Hirn & Grust: "One WITH RECURSIVE Is Worth Many GOTOs", SIGMOD 2021.** "Trampolined style" compiles arbitrarily nested iterative control flow into WITH RECURSIVE; about 2x speedups; PostgreSQL v11+. A GitHub repository holds **sample PL/SQL UDFs and their compiled SQL**, not necessarily the compiler, and the paper has ACM artifact badges. — [Tübingen page](https://db.cs.uni-tuebingen.de/publications/2021/one-with-recursive-is-worth-many-gotos/).
- **Hirn: "Data is Data and Control Should be Data, Too — Compiling Iterative Table-valued PL/SQL UDFs into Recursive SQL Code", VLDB 2023 PhD Workshop.** Covers table-valued UDFs that use `RETURN NEXT` or `RETURN QUERY`. Each iteration of the recursive CTE emits a "control row" plus "data rows" (`data?` = true and `res` = the returned value). — [Tübingen page](https://db.cs.uni-tuebingen.de/publications/2023/data-is-data/); [PDF](https://db.cs.uni-tuebingen.de/publications/2023/data-is-data/trampolined-data-control.pdf).
- **Hirn: PhD thesis "New Compilation Methods for Complex User-Defined Functions", Univ. Tübingen 2024.** Iterative UDFs give up to 3x; recursive UDFs up to 180x. Embedded queries are treated as a "parameterized black box", with parameters substituted only at the last step.
  - The thesis has an explicit "Unsupported PL/SQL Features" list:
    - **Dynamic SQL** ("not compilable … would require modifying the source code of the current SQL query at runtime");
    - **Exception handling** ("SQL does not support exception handling");
    - **INSERT/UPDATE/DELETE** ("it is impossible to modify a table and read its modified state in the same query");
    - **DDL**;
    - **cursor loops**, described as "not technically impossible", with a qualification I could not read in full.
  - — [thesis PDF](https://db.cs.uni-tuebingen.de/publications/2024/new-compilation-methods-for-complex-user-defined-functions/dissertation_hirn_denis.pdf); [record](https://publikationen.uni-tuebingen.de/xmlui/handle/10900/154480).
- **Fischer, Hirn, Grust: "SQL Engines Excel at the Execution of Imperative Programs", PVLDB 17(13), 2024 ("Flummi").** Maps arbitrary branching and looping CFGs (programs of several hundred lines) to recursive SQL:1999 CTEs and runs them over batches of arguments. — [Tübingen page](https://db.cs.uni-tuebingen.de/publications/2024/flummi/). Related: "Snakes on a Plan" (Python→SQL, SIGMOD 2022); "BIRNE" (DBPL 2025); "Trampoline-Style Queries for SQL" (CIDR 2025); "Dear User-Defined Functions, …" (Franz, Arch, Hirn, Grust, Mowry, Pavlo, CIDR 2024); "Functions are Data Too: Defunctionalization for PL/SQL" (Grust, Schweinsberg, Ulrich, VLDB 2013). — [Tübingen UDF-compilation project](https://db.cs.uni-tuebingen.de/research/current-projects/plsql/); [publication list](https://db.cs.uni-tuebingen.de/publications/).

**Construct frequencies in real workloads**
- **Gupta & Ramachandra, PVLDB 14(8) 2021** report the following.
  - Loops: "roughly 11% procedures, 7% UDFs and 8% triggers contain at-least one loop (cursor/while)".
  - Nested IF: 21% of procedures have a nested IF block, with a maximum nesting depth of 90.
  - Statement counts: procedures average 13.09 statements (90th percentile 30, maximum 343).
  - On inlining: "the SQL translation techniques cannot handle DML and exception handling constructs, which are fairly common in stored procedures".
  - — [PDF](https://www.vldb.org/pvldb/vol14/p1378-ramachandra.pdf).

**Formal semantics**
- No formal semantics of PL/SQL, PL/pgSQL or SQL/PSM turned up in searching. SQL/PSM (ISO/IEC 9075-4) standardises control flow, condition handling, local variables, assignment and cursors. — [Wikipedia SQL/PSM](https://en.wikipedia.org/wiki/SQL/PSM). Halder & Cortesi's denotational semantics of programs with embedded DML (Q1) is the closest formal treatment found.

### Inferences
- **Whole-routine inlining is not viable as the general fix.**
  - Froid has no DML, no loops and no cursors.
  - Tübingen has no DML, DDL, exceptions or dynamic SQL, and has only partial cursor support.
  - Most of our hard cases are side-effecting or non-functional: triggers with `NEW.x :=`, `COPY`, procedures doing INSERT/UPDATE, `EXECUTE format()`, `\gset`.
  - Even where inlining works, the output is LATERAL chains or WITH RECURSIVE trampolines. Column lineage through those with SQLGlot would itself be fragile, because recursive CTEs carry control columns mixed with data columns.
- **Adopt their IR, not their output.** The SSA step ("diversity of control-flow constructs → single goto", expressions stay SQL expressions, embedded queries are parameterised black boxes) is the canonical-IR recipe in the brief. A minimal IR would be:
  - **(a) assign**: `x := <SQL expr over vars>`;
  - **(b) query-bind**: `x1..xn := <SQL query with var params>`, which covers SELECT INTO, FETCH, FOR-over-query rows and GET DIAGNOSTICS;
  - **(c) emit**: append rows to the routine result. This covers RETURN, RETURN NEXT (record built field by field), RETURN QUERY, OUT/INOUT params, `NEW` in BEFORE triggers, and the BEGIN ATOMIC final SELECT;
  - **(d) effect**: `table.col ⊇ <query>`, covering INSERT/UPDATE/DELETE/MERGE/COPY;
  - **(e) call**: call another routine with arguments, resolved through summaries;
  - **(f) dyn**: execute a string expression, resolved by string analysis (Q4) or by fallback;
  - **goto/branch with condition**, which carries filter dependencies;
  - **opaque**: an unknown construct, which gets conservative edges.
- Each current desugar rule would then become a *lowering* to these few node types. One monotone dataflow pass (an abstract value is a set of (source column, flow kind) pairs, joined by union at phi nodes) computes lineage. Embedded SQL is still analysed by SQLGlot, with parameters bound to the abstract values of the SSA variables.
- Froid's per-statement "derived table with variables as columns" encoding shows how a straight-line, side-effect-free scalar or set-returning function *could* be inlined as a lineage-preserving SQL rewrite (LATERAL chain). This is useful for routines used as row sources in FROM/LATERAL, where we need output column → input column mappings of the function result.
- Aggify's loop→aggregate idea is a semantic lens for accumulators: a variable updated in a loop over a query is an aggregate over that query's columns. In lineage terms, the accumulator depends on all columns it reads, with flow kind "aggregate".

### Gaps
- I could not confirm whether the Tübingen *compiler itself* (rather than sample UDFs and compiled outputs) is open source, or under what licence. No repository URL was found on the project or paper pages.
- I did not find a published formal operational semantics of PL/pgSQL; one may exist in theses not indexed by these searches.
- The QBS / Cheung et al. synthesis of SQL from imperative code (PLDI 2013) and the Cobra/K. Ramachandra holistic-optimisation papers were not verified in this session.

---

## Q4. Static analysis of dynamic SQL (string analysis)

### Takeaway
The classic approach approximates the set of query strings a program can build: a grammar or regular language from Christensen/Møller/Schwartzbach (JSA), which Gould/Su/Devanbu then type-check against the schema. The same approach transfers to `EXECUTE format(...)` and string concatenation in PL/pgSQL. The PL/SQL→SQL compilers declare dynamic SQL out of scope. When the string cannot be resolved statically, the literature falls back to dynamic analysis (Cleve & Hainaut).

### Cited Findings
- **Christensen, Møller, Schwartzbach: "Precise Analysis of String Expressions", SAS 2003.** Implements static analysis that over-approximates the strings produced by each Java string expression as a regular language. It builds a flow graph from class files, derives a context-free grammar with one nonterminal per string expression, and widens the grammar to a regular language with the Mohri-Nederhof approximation. One stated application is syntax-checking dynamically generated SQL. — [paper page](https://cs.au.dk/~amoeller/papers/strings/); [BRICS report](https://www.brics.dk/RS/03/5/BRICS-RS-03-5.pdf).
  - Tool: **JSA (Java String Analyzer)**, latest version 2.1-1, © 2003-2009 Aarhus University, built on dk.brics.automaton. The page did not state a licence explicitly. — [JSA site](https://www.brics.dk/JSA/).
- **Gould, Su, Devanbu: "Static Checking of Dynamically Generated Queries in Database Applications", ICSE 2004.** A sound static analysis (soundness results claimed). It takes JSA's finite-state automaton of possible query strings and checks it with a modified context-free-language reachability algorithm against SQL syntax and **schema types**. Examples: catching a string compared with an integer column, or a subquery that returns two columns where one is expected. — [PDF](https://www.cs.ucdavis.edu/~su/icse.pdf).
- **Cleve & Hainaut: "Dynamic Analysis of SQL Statements for Data-Intensive Applications Reverse Engineering", WCRE 2008.** Notes that static SQL is easy to analyse but dynamic SQL "most often require[s] dynamic analysis". Uses aspect-based tracing and SQL trace analysis. — [UNamur portal](https://researchportal.unamur.be/fr/publications/dynamic-analysis-of-sql-statements-for-data-intensive-application). Related: "Static analysis of dynamic database usage in Java systems" (UNamur). — [UNamur](https://researchportal.unamur.be/fr/publications/static-analysis-of-dynamic-database-usage-in-java-systems).
- **Nagy & Cleve: "SQLInspect: A Static Analyzer to Inspect Database Usage in Java Applications", ICSE 2018 demo track.** An Eclipse plug-in that extracts SQL from Java/JDBC by static string analysis, parses it, and maps database objects to the code that builds the queries. Supports MySQL and Impala dialects. — [UNamur portal](https://researchportal.unamur.be/en/publications/sqlinspect-a-static-analyzer-to-inspect-database-usage-in-java-ap); [IEEE CSDL](https://computer.org/csdl/proceedings-article/icse-companion/2018/566301a093/13bd1gzWkRM).
- **Wei, Muthuprasanna, Kothari: "Preventing SQL injection attacks in stored procedures", ASWEC 2006 (Iowa State).** Addresses injection through dynamic SQL inside stored procedures, the "database layer". — [record](https://hds2test.hebis.de/ulbda/EdsRecord/edseee%252Cedseee.1615052). I did not verify the method details; my recollection is static analysis of the procedure combined with runtime checking, but that is unconfirmed.
- The Tübingen compilers mark dynamic SQL as "not compilable" ([Hirn thesis](https://db.cs.uni-tuebingen.de/publications/2024/new-compilation-methods-for-complex-user-defined-functions/dissertation_hirn_denis.pdf)), and the CIDR paper says it "will probably never compile to SQL" ([CIDR 2020](https://www.vldb.org/cidrdb/papers/2020/p1-duta-cidr20.pdf)).

### Inferences
- For PL/pgSQL, a three-tier treatment fits the literature:
  1. **Constant folding / partial evaluation** of `format()`, `||` and `quote_ident()` over constants and SSA-propagated constants gives the exact SQL, which then goes into the normal pipeline. This generalises the existing "constant format() text" rule into one IR pass.
  2. **Grammar or regular approximation** (JSA-style) when the string varies over a finite set, such as a table name chosen by IF or a loop over an array literal. Enumerate the finite language, or intersect it with the catalog's table and column names, then union the lineage of every instance.
  3. **Conservative fallback**: emit "unverified/influence" edges from every catalog object whose name occurs in a literal fragment, or from every argument variable, rather than refusing. Gould et al.'s schema-aware check is the precedent for using the catalog to prune.
- Runtime capture (Cleve & Hainaut style, e.g. `pg_stat_statements` or log tracing) is a possible optional enrichment. It would sit outside the static pipeline.

### Gaps
- I found no paper that applies string analysis to PL/pgSQL `EXECUTE` specifically.
- Wassermann & Su (PLDI 2007, grammar-based analysis of PHP query construction) and abstract-interpretation string domains (e.g. Costantini/Ferrara/Cortesi) were not verified in this session.
- I could not confirm JSA's licence.

---

## Q5. Datalog / Soufflé / CodeQL dataflow frameworks applied to SQL or database code

### Takeaway
I found no published Doop-style Datalog points-to or dataflow analysis *of SQL or stored-procedure code*. CodeQL does not support SQL or PL/SQL as an analysis language; it only models SQL sinks in host languages. The available precedents are Datalog-as-engine (Soufflé), Tomingas's rule-based transitive closure written in SQL, and the reverse direction (compiling Datalog analyses into recursive SQL).

### Cited Findings
- **CodeQL's supported languages are** C/C++, C#, GitHub Actions, Go, Java, Kotlin, JavaScript, Python, Ruby, Rust, Swift and TypeScript. There is no SQL, PL/SQL, T-SQL or PL/pgSQL; database libraries (JDBC, MyBatis, etc.) are modelled only as frameworks of host languages. — [CodeQL supported languages](https://codeql.github.com/docs/codeql-overview/supported-languages-and-frameworks/).
- **Soufflé** synthesises parallel C++ from Datalog specifications for static analysis of large code bases. — [Wikipedia](https://en.wikipedia.org/wiki/Souffl%C3%A9_(programming_language)); [PLDI 2017 tutorial "Engineering Static Analyzers with Soufflé"](https://pldi17.sigplan.org/event/pldi-2017-workshops-and-tutorials-engineering-static-analyzers-with-souffl-).
- **"Compiling Linear Datalog to SQL for Program Analysis" (arXiv 2609.06301, 2026)** advocates recursive SQL as a backend for Datalog program analyses. It is the reverse direction from ours but shows the program-analysis-in-SQL idea is current. — [arXiv](https://arxiv.org/pdf/2609.06301).
- **Tomingas et al. (2016)** implemented their lineage propagation and transitive-closure rules as SQL in PostgreSQL. — [KDIR PDF](https://www.scitepress.org/PublishedPapers/2016/60543/60543.pdf).

### Inferences
- Once routines are lowered to the small IR in Q3, the IR facts (`assign(x, srcs)`, `bind(xs, query_id)`, `emit(routine, col, srcs)`, `effect(table.col, srcs)`, `call(site, routine, args)`) and the lineage rules (transitive def-use, interprocedural summaries, trigger firing) are naturally a Datalog program. That could be Soufflé, a Python Datalog, or recursive SQL in the existing Neo4j/Postgres backend. A Datalog formulation makes the "one generic analysis" auditable and gives fixpoints over recursion and loops for free. This is a design inference, not an established result for SQL.

### Gaps
- I did not find a Doop- or Soufflé-based analyser for PL/SQL or T-SQL in the literature. A dedicated search (e.g. "Datalog" plus "PL/SQL") might surface theses.

---

## Q6. Lineage benchmarks and evaluation datasets (SQL and procedural), coverage and accuracy evaluation

### Takeaway
**No public benchmark with ground-truth column lineage for stored procedures was found.**
- SQL-ProcBench (Microsoft, 2021) is the best public *corpus* of realistic procedural code in PL/pgSQL, PL/SQL and T-SQL, but it has no lineage labels.
- LineageX (SFU, 2025) evaluates SQL-query column lineage and does not target procedures.
- Microsoft's 2025 schema-lineage benchmark has 1,700 hand-annotated lineages over 50 enterprise scripts (SQL, Python, C#), but it is LLM-oriented and not released.
- Froid's 59.8% figure is the only large published *coverage* number for procedural constructs.

### Cited Findings
- **Gupta & Ramachandra: "Procedural Extensions of SQL: Understanding their Usage in the Wild", PVLDB 14(8):1378-1391, 2021 (DOI 10.14778/3457390.3457402).**
  - Static analysis of thousands of real procedures, UDFs and triggers along 38 features in 10 categories: control flow, DML (Select/Insert/Update/Delete/Merge), DDL, transaction statements, table variables, etc.
  - The Azure SQL DB object mix is stored procedures 69%, UDFs 24%, triggers 7%.
  - **SQL-ProcBench** has 103 objects: 63 stored procedures, 24 scalar UDFs, 10 table-valued UDFs and 6 triggers. It has inter-object dependency graphs and is available in **T-SQL, PL/SQL and PL/pgSQL**.
  - — [PDF](https://www.vldb.org/pvldb/vol14/p1378-ramachandra.pdf); repo [github.com/microsoft/sql-procbench](https://github.com/microsoft/sql-procbench), archived June 2026 but readable (search snippet). The paper is CC BY-NC-ND 4.0; the repo licence was not verified.
- **Froid's coverage evaluation:** 51,047 / 85,329 scalar UDFs (59.8%) inlinable; in 10,526 customer databases with more than 50 UDFs, Froid could inline more than 70%. — [arXiv 1712.00498](https://arxiv.org/pdf/1712.00498).
- **Zhang, Miao, Wang: "LineageX: A Column Lineage Extraction System for SQL", arXiv 2505.23133, 2025 (SFU).** Python library. It parses with SQLGlot and traverses the AST with a comprehensive rule set to infer column-level lineage, plus visualisation. Examples are drawn from the MIMIC database. It is positioned against SQLLineage, ProvSQL, Perm and GProM. The readable excerpt did not mention stored procedures or PL/pgSQL. — [arXiv](https://arxiv.org/pdf/2505.23133); repo [github.com/sfu-db/lineagex](https://github.com/sfu-db/lineagex). The "CC-BY-4.0" licence reported by the fetch tool may refer to the arXiv paper, not the code; verify before relying on it.
- **Yin, Chen, Lee, Liu (Microsoft): "Schema Lineage Extraction at Scale: Multilingual Pipelines, Composite Evaluation, and Language-Model Benchmarks", arXiv 2508.07179, Aug 2025.** 1,700 manually annotated schema lineages from 50 real enterprise scripts in SQL, Python/PySpark and C#, at three difficulty levels. Proposes the **SLiCE** composite metric: format, source schemas, source tables, transformation logic and aggregation. Evaluates 12 open LLMs (1.3B-32B) plus GPT-4o and GPT-4.1. Accuracy drops with script complexity. The data is not public; only simulated examples appear in the appendices. — [arXiv](https://arxiv.org/html/2508.07179v1). (LLM-based; listed only for awareness.)
- **"A Large Language Model-Based Approach for Data Lineage Parsing", Electronics 14(9):1762, 2025 (MDPI).** An LLM-based lineage parser; the full text was blocked, so I could not check its stored-procedure coverage or dataset. — [MDPI](https://www.mdpi.com/2079-9292/14/9/1762).
- **"Lineage-Aware Memory Governance…" (arXiv 2610.07258, Oct 2026)** states that stored procedures and UDFs invoked from SQL are "opaque to AST-level column extraction", which is a recent acknowledgement of the gap. — [arXiv](https://arxiv.org/html/2610.07258).
- **Tomingas et al. 2016** evaluated on six industrial datasets, two of them Oracle stored-procedure warehouses, but measured only throughput and graph size, with no accuracy. — [KDIR PDF](https://www.scitepress.org/PublishedPapers/2016/60543/60543.pdf).
- **Maule et al. 2008** is one of the few database-program static analyses that reports false negatives and false positives: none and low on one case study. — [academia.edu](https://www.academia.edu/60290609/Impact_analysis_of_database_schema_changes).

### Inferences
- SQL-ProcBench's 103 PL/pgSQL objects are a ready-made, unbiased blind corpus for *construct coverage* (refusal rate per object) of the routine-semantics layer. Ground-truth column lineage would need hand annotation, or dynamic provenance as an oracle (Q2).
- Reporting coverage the way Froid did (share of real routines fully handled, plus a breakdown of the constructs that block them) would be directly comparable to the only published numbers.
- An IR-plus-fallback design changes the metric from "refused or not" to "exact / conservative-fallback / refused" per routine. This is worth defining before measuring.

### Gaps
- I found no lineage-labelled procedural benchmark and no published evaluation of SQLLineage or OpenLineage-style parsers on stored procedures.
- I did not find LineageX's numeric accuracy in the readable excerpt.
- The "Data Lineage Extraction Techniques for SQL-Based Systems" (ResearchGate, 2025) item looked low-quality and was not read.

---

## Q7. Surveys that discuss procedural code as an open problem

### Takeaway
The standard provenance survey (Herschel et al. 2017) frames provenance by use, form and capture source. Beyond the individual papers above, I did not find a survey that singles out stored procedures or PL code as an open lineage problem. The clearest "open problem" statements are inside the compile-to-SQL papers (dynamic SQL, exceptions, DML out of scope) and in Gupta & Ramachandra's workload study.

### Cited Findings
- **Herschel, Diestelkämper, Ben Lahmar: "A survey on provenance — What for? What form? What from?", VLDB Journal 26(6):881-906, 2017.** — [VLDBJ](https://vldb.org/vldb_journal/index.php/component/remository/Published-Articles/Volume-26/Number-6/A-survey-on-provenance-What-for-What-form-What-from/). I did not read its full text this session.
- **Gupta & Ramachandra 2021** state that "little was known about the magnitude of usage and complexity of procedural SQL". They note that existing translation techniques (Froid, Aggify, the Tübingen compilers) give "limited coverage" because they "cannot handle DML and exception handling constructs, which are fairly common in stored procedures". — [PDF](https://www.vldb.org/pvldb/vol14/p1378-ramachandra.pdf).
- **Tang et al. 2023** state that current RDBMS provenance work "still ha[s] limitations in supporting full-featured SQL or procedural languages". — [MDPI](https://www.mdpi.com/2076-3417/13/1/64) (abstract via search snippet).
- **"A Comprehensive Survey on the State-of-the-art Data Provenance Approaches for Security Enforcement"** (arXiv 2107.01678) exists; not read. — [arXiv](https://arxiv.org/pdf/2107.01678).

### Inferences
- The literature points to the same split that the brief proposes. Side-effect-free, structured control flow is well understood: SSA/ANF and Apply/LATERAL algebrisation. DML, exceptions, cursors and dynamic SQL are where every system stops. A static lineage analyser does not need *semantic equivalence* (which the compilers need), only a sound over-approximation of dependencies. So it can handle exactly the constructs the compilers reject:
  - DML becomes effect edges;
  - an exception handler is just another CFG successor of every statement in the protected block;
  - a cursor is a query-bind in a loop;
  - dynamic SQL is string analysis plus fallback.
  This is the main architectural conclusion from the literature review.

### Gaps
- I did not find a 2024-2026 survey specifically on column-level lineage tools. It may exist in industry or grey literature, which the parallel "vendors_and_practitioners" notes cover.
