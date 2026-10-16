# Procedural SQL column lineage: vendor and practitioner approaches (as of Oct 2026)

Scope note: this covers how commercial vendors and practitioners handle column-level lineage through stored procedures and procedural SQL (PL/pgSQL, PL/SQL, T-SQL), what they claim about coverage, and why practitioners say it is hard. Every claim has a source. **[VERIFIED-DOC]** marks a vendor's own technical documentation, which is usually reliable on limitations. **[MARKETING]** marks vendor promotional copy or vendor-written comparisons. **[SNIPPET]** marks text seen only as a search-result summary, not on a page I fetched in full. About 35 tool calls; several vendors on the list (Octopai, SQLDep, Select Star, Secoda, CastorDoc, Datafold, Monte Carlo, Foundational, erwin, Ab Initio, Solidatus, AWS DataZone, OCI Data Catalog) were not researched. They are listed under Gaps.

---

## 1. MANTA / IBM Manta Data Lineage: scanners, PL/pgSQL, dynamic SQL, IR, open source

### Takeaway
IBM's own documentation for the Manta-based lineage product is plain about limits. For PostgreSQL it processes SQL and PL/pgSQL *functions*, scripts and views. Triggers and procedures are not listed. Dynamic SQL run through EXECUTE is "not processed" for both PostgreSQL and Oracle. Manta's engine uses a "modular symbolic data flow analysis" (described at ICSE 2024 for its Java/C# scanners). A 2018 Czech Technical University thesis added pattern-based dynamic-SQL strategies to Manta "in some cases". I found no open-source Manta components.

### Cited Findings
- **[VERIFIED-DOC]** PostgreSQL lineage in IBM's Manta-based product processes "Data dictionaries, Scripts, Views, Functions (SQL and PL/pgSQL), Custom types". Triggers and procedures are **not listed**. — [IBM Docs: PostgreSQL lineage configuration](https://www.ibm.com/docs/en/DSXDOC/wsj/lineage/lineage-postgresql.html)
- **[VERIFIED-DOC]** Stated PostgreSQL limitations, verbatim: "Assets that use dynamically executed code through the EXECUTE IMMEDIATE statement are not processed." and "EDB assets with source code obfuscated using the EDB*Wrap utility are not processed." Unobfuscated source can be supplied manually as external input. — [IBM Docs: PostgreSQL lineage configuration](https://www.ibm.com/docs/en/DSXDOC/wsj/lineage/lineage-postgresql.html)
- **[VERIFIED-DOC]** The PostgreSQL page does not mention column-level lineage at all. — [IBM Docs: PostgreSQL lineage configuration](https://www.ibm.com/docs/en/DSXDOC/wsj/lineage/lineage-postgresql.html)
- **[SNIPPET]** The IBM Oracle lineage page says the same: highly dynamic SQL run by EXECUTE IMMEDIATE is not processed by the Oracle scanner. — [IBM Docs: Oracle lineage configuration](https://www.ibm.com/docs/en/DSXDOC/wsj/lineage/lineage-oracle.html)
- **Technical (peer-reviewed venue):** Pavel Parízek (Charles University) and Lukáš Hermann (Manta) presented "Data Lineage Analysis for Enterprise Applications by Manta: The Story of Java and C#" at ICSE 2024 SEIP. The talk describes scanners that turn programs into data-flow graphs using "modular symbolic data flow analysis", plus the challenges of real enterprise code. No DOI is shown on the program page. — [ICSE 2024 SEIP program](https://conf.researchr.org/details/icse-2024/icse-2024-software-engineering-in-practice/28/Data-Lineage-Analysis-for-Enterprise-Applications-by-Manta-The-Story-of-Java-and-C-)
- **Academic:** A 2018 CTU Prague master's thesis by Artur Nasyrov (supervisor Jan Janoušek), "Analysis of Dynamic SQL Code in Manta Project". Abstract: "analyze the typical cases of dynamic SQL usage in the data warehouses and to extend the Manta project with a set of strategies which allow dynamic SQL analysis in some cases." The specific strategies were not visible on the metadata page. — [CTU DSpace record](https://dspace.cvut.cz/entities/publication/927f2e83-cbe4-4155-a37e-b1d7bbd34de6); [alt record](https://dspace.cvut.cz:443/handle/10467/76796?show=full)
- A Charles University software-project specification for a Manta C# scanner exists. I did not read it. — [mantacs specification PDF](https://www.ksi.mff.cuni.cz/sw-projekty/zadani/mantacs_specification.pdf)
- **[SNIPPET]** Third parties OEM Manta. data.world's "technical lineage server" is "powered by MANTA" and connects to databases, ETL and BI tools. Its Manta collector harvests "stored procedures with tabular results and tables referenced by each procedure". — [data.world: Technical lineage server (MANTA)](https://docs.data.world/en/98554-technical-lineage-server-manta.html); [data.world: About the MANTA collector](https://docs.data.world/en/187038-about-the-manta-collector.html)
- An IBM Community blog titled "Mastering dynamic SQL for automated data lineage" (2024) names Manta only in passing ("offer specialized features for handling certain dynamic SQL patterns"). It gives no mechanism. Its recommendations are generic: logging, code annotations, SQL precompilation and query-log extraction. — [IBM Community blog](https://community.ibm.com/community/user/blogs/joe-chmielewski/2024/08/28/mastering-dynamic-sql-for-automated-data-lineage)
- **[SNIPPET]** User reviews on Capterra and Software Advice list dynamic SQL support as an area for improvement. — [Software Advice Manta reviews](https://www.softwareadvice.com/bi/manta-flow-profile/reviews/)
- IBM support pages use the product name "IBM Manta Data Lineage". — [IBM Support: IBM Manta Data Lineage](https://www.ibm.com/support/pages/ibm-manta-data-lineage)

### Inferences
- Even the market leader does not claim general resolution of dynamic SQL in PL/pgSQL or PL/SQL. Its documented position is "not processed". The thesis suggests that, internally, only common patterns (probably constant or concatenation strings) are resolved. Our 77% blind recall should be read against that baseline: the industry leader documents gaps in exactly the area where we are weakest.
- Manta's architecture, inferred from the ICSE talk and the "scanner per technology" description, is a separate scanner per language. Each scanner lowers code into a shared data-flow graph and runs symbolic data-flow analysis over it. That is an IR plus analysis, not one rule per syntactic form. This is the main architectural lesson for us. The ICSE talk covers Java and C#; that the SQL scanners use the same approach is an inference.
- IBM documents PL/pgSQL *functions* but not procedures or triggers. Either PostgreSQL support is shallower than Oracle or Teradata, or the documentation lags the product.

### Gaps
- I found no Manta patent and no published Manta IR specification.
- I found no open-source or community edition of Manta. I did not verify the acquisition date (IBM, 2023) against a primary source in this session.
- I found no published Manta coverage or accuracy numbers.
- I could not see the thesis's actual dynamic-SQL strategies; the full PDF was not retrieved.

---

## 2. Gudu SQLFlow / General SQL Parser (GSP): procedural depth, dynamic SQL, licensing

### Takeaway
Gudu is the only vendor found that explicitly markets full procedural-language parsing for PL/pgSQL, PL/SQL and T-SQL. That includes variable and %TYPE resolution, BULK COLLECT, temp tables from SELECT INTO, and simple dynamic SQL (`EXECUTE format()` in Postgres). The engine is a commercial Java library (GSP). The Python wrapper is free only for non-commercial use, with a 10k-character SQL cap. Gudu publishes no coverage numbers beyond a vendor-written 14-case parser comparison.

### Cited Findings
- **[MARKETING]** SQLFlow "supports analyzing stored procedures and dynamic SQL statements" in 20+ dialects, Postgres included. — [componentsource: Gudu SQLFlow](https://www.componentsource.com/product/gudu-sql-flow/about); [Gudu blog: What is SQLFlow](https://www.dpriver.com/blog/?p=1751)
- **[VERIFIED-DOC]** `python_data_lineage` ("Gudu SQLFlow Lite for Python") bundles a Java library (the GSP engine) with Python bindings and JS visualisation. Requirements: JDK 1.8+ and Python 3; no database connection is needed. It is "free for non-commercial use", handles SQL "up to 10k" characters, and commercial use requires a license from Gudu. The README shows Postgres dynamic SQL via `EXECUTE format()` and supports PL/SQL and plpgsql. — [GitHub: sqlparser/python_data_lineage](https://github.com/sqlparser/python_data_lineage)
- **[MARKETING]** Gudu's blog post "Why Most SQL Lineage Tools Skip Stored Procedures" claims GSP:
  - "parses the full procedural language for each supported dialect"
  - builds internal representations of variable declarations and data flow
  - "resolves variable types through `%TYPE` and record declarations"
  - tracks BULK COLLECT into collections
  - resolves temp-table schemas from SELECT INTO
  - covers Oracle PL/SQL, T-SQL, PL/pgSQL, MySQL and DB2 SQL PL, with 34 dialects in its VS Code plugin

  It gives no test counts. — [Gudu blog p=3177](https://www.dpriver.com/blog/?p=3177)
- **[MARKETING]** The same post says what makes procedures hard. Lineage "passes through collection variables, loop indices, and conditional branches", and the vendor syntax is specific: PL/SQL packages, T-SQL TRY/CATCH and table variables, PostgreSQL PERFORM and RETURN QUERY. It also says open-source metadata platforms have long-open stored-procedure-lineage issues and that catalogs "show procedure code but lack parsing for lineage". — [Gudu blog p=3177](https://www.dpriver.com/blog/?p=3177)
- **[MARKETING, vendor-authored benchmark]** "GSP vs JSQLParser vs sqlglot (2026)" uses 14 real-world patterns across 6 dialects. Results: GSP 14/14, sqlglot 11/14, JSQLParser 11/14. Per the post, sqlglot handles PL/pgSQL RETURN QUERY and BigQuery DECLARE/IF. It falls back to `Command` for T-SQL procedure bodies, losing structure, and cannot parse Oracle BULK COLLECT. This is a parse-success test, not a lineage-accuracy test. — [Gudu blog: GSP vs JSQLParser vs sqlglot](https://www.dpriver.com/blog/gsp-vs-jsqlparser-vs-sqlglot-sql-parser-comparison-2026/)
- **[SNIPPET]** GSP is a commercial Java (and .NET) SDK; pricing is not public ("contact vendor"). — [G2: General SQL Parser pricing](https://g2.com/products/general-sql-parser/pricing); [NuGet gudusoft.gsqlparser](https://www.nuget.org/packages/gudusoft.gsqlparser/)
- The older Gudu blog post on stored-procedure lineage shows only a simple T-SQL example (parameter to target column). It does not discuss cursors, temp tables or dynamic SQL. — [Gudu blog p=2923](https://www.dpriver.com/blog/?p=2923); see also the [Oracle procedures post](https://www.dpriver.com/blog/?p=2012)

### Inferences
- GSP is the only licensable engine found that claims PL/pgSQL procedural data-flow. Licensing it would add a JVM dependency and a commercial OEM licence, and the free Python wrapper cannot be used commercially. It would also be worth testing against our blind corpora before trusting the marketing claims.
- Gudu's own list of hard constructs (collections, loop indices, branches, PERFORM, RETURN QUERY, temp tables via SELECT INTO, %TYPE) works as a checklist for our rule set.

### Gaps
- No precise GSP or SQLFlow licence price. I did not find the separate `dlineage`/DataFlowAnalyzer licence terms beyond "commercial; free non-commercial Python lite".
- No published lineage precision or recall for Gudu, and no public procedural test suite. Gudu's GitHub test cases were not examined.

---

## 3. Catalog and governance vendors (static parsing): Collibra, Informatica, Alation, Atlan, Dataedo, Ataccama, DataHub, Metaplane

### Takeaway
Most catalog vendors offer stored-procedure lineage only in specific forms: harvester-dependent (Collibra), beta (Alation), statement-subset (Dataedo), or not at all (DataHub's sqlglot-based parser explicitly excludes multi-statement scripts and procedures). All of them that document limits exclude dynamic SQL. PostgreSQL procedure support is often weaker than Oracle or SQL Server.

### Cited Findings
- **[VERIFIED-DOC] Collibra:**
  - PostgreSQL: "SQL-based input and stored procedures" via Edge, but "SQL-based input without stored procedures" via the lineage harvester.
  - Oracle, SQL Server and Redshift: SQL input plus stored procedures under both methods.
  - Greenplum: without stored procedures.
  - "Technical lineage cannot be created for views and procedures if the SQL definitions are encrypted."

  Functions and dynamic SQL are not mentioned. — [Collibra: Supported data sources for technical lineage](https://productresources.collibra.com/docs/collibra/latest/Content/CollibraDataLineage/ref_technical-lineage-supported-data-sources.htm). Collibra also publishes a "Supported SQL statements" page, which I did not fetch. — [Collibra: Supported SQL statements](https://productresources.collibra.com/docs/release-notes/Content/CollibraDataLineage/TechnicalLineage/ref_data-lineage-sql-statements.htm)
- **[SNIPPET/MARKETING] Informatica (EDC/CDGC Advanced Scanners):**
  - Advanced Scanners "parse code from various stored procedures" and "extract deep metadata from both static and dynamic code".
  - Lineage gives "full visibility into the procedure calls with parameter tracking, dynamic SQL generation from values based on parameters". — [Informatica success accelerator: Advanced scanners for SQL scripts](https://success.informatica.com/success-accelerators/advance-scanners-for-sql-scripts-.html); [Informatica blog: Advanced Scanners](https://www.informatica.com/blogs/leave-no-metadata-behind-with-ai-powered-data-catalog-advanced-scanners.html)
  - Informatica's site attributes them to its CLAIRE AI engine. — [Informatica EDC Advanced Scanners webinar](https://www.informatica.com/ca/about-us/webinars/reg/informatica-enterprise-data-catalog-advanced-scanners-3878153.html)
  - Architecture slides exist (not read): [Tech Tuesdays PDF](https://www.informatica.com/content/dam/informatica-cxp/techtuesdays-slides-pdf/Advanced%20Scanners-Architecture%20Installation%20Capabilities%20and%20Updates.pdf)
- **[VERIFIED-DOC] Alation:**
  - "Lineage for Stored Procedures (Beta)", added in 2024.3.3.
  - Supported for Redshift, Azure SQL DB, Synapse, SQL Server, Oracle (connector 1.9.1+), **PostgreSQL**, SAP HANA, Snowflake and Vertica.
  - Documented limits: `SELECT *` may lose column-level info; "orphan columns" can drop source tables from the graph.
  - The method (static or log-based) and column-level depth are not stated. — [Alation: Lineage for Stored Procedures (Beta)](https://docs.alation.com/en/latest/sources/Lineage/StoredProcedureLineage.html)
- **[SNIPPET] Atlan:** Atlan has FAQ pages on "SQL dump lineage support" and "table level lineage only" cases, and its docs say column-level accuracy varies by connector. — [Atlan: what is column-level lineage](https://docs.atlan.com/product/capabilities/lineage/concepts/what-is-column-level-lineage.md); [Atlan: table-level lineage only FAQ](https://docs.atlan.com/product/capabilities/lineage/faq/table-level-lineage-only.md)
- **[VERIFIED-DOC] Dataedo, SQL Server:**
  - Lineage combines imported metadata, dependency metadata and T-SQL parsing. Scripts are split into process-like steps.
  - "lineage is created only for supported statements". Dynamic SQL and runtime-generated statements are not parsed. Unsupported T-SQL syntax interrupts parsing; partial parsing happens, and unsupported statements appear as separate steps named after their first keyword.
  - Guidance: "automatic lineage should be the baseline, then enriched with governance review".

  — [Dataedo: Automatic Data Lineage in SQL Server](https://dataedo.com/kb/databases/sql-server/automatic-data-lineage-in-sql-server)
- **[VERIFIED-DOC] Dataedo, PL/SQL:**
  - In procedures it supports INSERT, UPDATE, RETURNING INTO, non-recursive CTEs, CTAS, and lineage from procedure parameters.
  - **Not supported:** "DELETE statements, MERGE statements, INSERT ... WHEN ...", PIVOT/UNPIVOT, recursive CTEs, **dynamic queries** and window functions.
  - It is ANTLR-based: "the presence of unsupported elements in a procedure may prevent it from being parsed entirely."

  — [Dataedo: PL/SQL parsing](https://docs.dataedo.com/data-sources/sql-parser/plsql-parsing); see also [Automatic column-level lineage from imported Stored Procedures](https://dataedo.com/docs-23/automatic-column-level-lineage-from-imported-stored-procedures)
- **[VERIFIED-DOC] Ataccama ONE, Oracle scanner:**
  - It supports design-time lineage for procedures, functions and packages.
  - "Lineage is not supported for: Dynamic SQL". Also unsupported: table functions, DB links, synonyms, CONNECT BY, MATCH_RECOGNIZE, multi-column pivot.

  — [Ataccama: Oracle lineage scanner](https://docs.ataccama.com/one/16.1.0/lineage/oracle-lineage-scanner.html)
- **[VERIFIED-DOC] DataHub (open source, Acryl):**
  - Its parser is built on sqlglot with schema-aware resolution (`SELECT *` expansion).
  - It claims "97-99% accuracy and outperforms other SQL parsers by a wide margin" and emits a 0-1 confidence score.
  - **Not supported:** "Multi-statement SQL / SQL scripting" (including stored procedures), scalar and table-valued UDFs, UNNEST, Snowflake multi-table insert, and dynamic SQL like `identifier()`. Temp tables need `SqlParsingAggregator`.

  — [DataHub: SQL parsing](https://docs.datahub.com/docs/lineage/sql_parsing)
- **Metaplane (practitioner/vendor engineering blog):**
  - Metaplane built its own ANTLR parser because "many problems could only be or were mostly easily solved at the parser level when building our AST."
  - Hard cases: `SELECT *` expansion against the live warehouse schema, nested CTEs, separating direct lineage from filter (WHERE) lineage, positional INSERT columns, and queries with hundreds of CTEs.
  - Its future work was "more complex update chains that pass through temporary tables".
  - "Building a comprehensive test suite was key."

  — [Metaplane: Column-level lineage, an adventure in SQL parsing](https://www.metaplane.dev/blog/column-level-lineage-an-adventure-in-sql-parsing)

### Inferences
- Across vendors that document limits, a stable pattern appears: statement-level coverage inside procedures, nothing for dynamic SQL, and failure isolation done at statement level (Dataedo shows unsupported statements as named steps). Dataedo's "unsupported statements appear as separate steps" is a useful idea for us: show the gap explicitly instead of dropping it silently. Our D-63 "unknowns in every answer" direction matches it.
- DataHub's 97-99% figure applies to single-statement SQL only. Because procedures are excluded, it is not comparable to our procedural recall.
- Informatica is the only vendor in this set that markets parameter-driven dynamic SQL resolution. It is unverified, and the AI (CLAIRE) branding suggests possible ML or heuristic components.

### Gaps
- I did not verify that Informatica Advanced Scanners are Manta OEM (a widely repeated claim); none of the Informatica pages retrieved mention Manta.
- I did not verify that Collibra's lineage engine originates from its 2019 acquisition of SQLdep; nothing retrieved confirmed it.
- Atlan's, Select Star's, Secoda's and CastorDoc's procedure handling was not documented in the results retrieved.

---

## 4. Platform-native runtime lineage: Microsoft Purview, Snowflake, Databricks, Google Dataplex

### Takeaway
The platform owners mostly avoid static analysis of procedural code and capture lineage from execution. Purview reads XEvent sessions on stored-procedure runs. Snowflake's ACCESS_HISTORY records column-level `directSources`/`baseSources` per executed statement and links nested procedure statements by `parent_query_id`/`root_query_id`. Databricks and Dataplex use query or job runtime too. All of them have hard gaps: temp tables, functions and triggers, DDL inside procedures, UDFs, and routines with no column lineage.

### Cited Findings
- **[VERIFIED-DOC] Microsoft Purview, Azure SQL Database:**
  - Stored-procedure lineage is "based on XEvent sessions", so Purview needs `db_owner` to manage them. It runs every 6 hours, and the frequency can't be changed.
  - "Lineage is extracted based on the stored procedure runs". It covers only runs after setup, and under heavy load only the most recent runs are kept.
  - Drill-down is "at SQL statement level within a stored procedure, along with column level lineage".
  - Limitations, verbatim: "not supported if your stored procedures contain an INSERT or a DROP query"; "Lineage is captured only when the stored procedure execution transfers data from one table to another. And it's not supported for temporary tables."; "Lineage extraction isn't supported for functions or triggers."; views referenced in procedures are captured as tables; and "If a stored procedure contains drop or create statements, they aren't currently captured in lineage".

  — [Microsoft Learn: Azure SQL Database in Purview (updated 2026-07-01)](https://learn.microsoft.com/en-us/purview/register-scan-azure-sql-database)
- Microsoft's launch blog on dynamic lineage from Azure SQL gives the rationale:
  - Purview taps "the instrumentation framework of the SQL engine" and extracts runtime logs.
  - Runs are enriched with run status, row counts, client and user.
  - Static analysis is insufficient because procedures may be "parametrized and uses dynamic SQL" or "a decision tree with an if then else statement executing different scripts at runtime".

  — [Azure blog: Introducing dynamic lineage extraction from Azure SQL Databases](https://azure.microsoft.com/blog/introducing-dynamic-lineage-extraction-from-azure-sql-databases-in-azure-purview/)
- **[VERIFIED-DOC] Snowflake ACCESS_HISTORY:**
  - `objects_modified` records column-level `directSources` and `baseSources` for writes (INSERT, MERGE, CTAS).
  - `parent_query_id`/`root_query_id` link statements run inside stored procedures, including nested calls, to the calling CALL. These columns were introduced in behaviour-change bundle 2023_08.
  - Lineage is tracked "provided that objects in the lineage chain are not dropped".

  — [Snowflake: Access History](https://docs.snowflake.com/en/user-guide/access-history); [Snowflake BCR 2023_08: parent_query_id/root_query_id](https://docs.snowflake.com/en/release-notes/bcr-bundles/2023_08/bcr-1265)
- **[SNIPPET/VERIFIED-DOC] Databricks Unity Catalog:**
  - Lineage is captured at runtime. Column lineage is supported only when source and target are referenced by table name, and "use of user-defined functions (UDFs) can obscure the mapping".
  - The system tables `system.access.table_lineage`/`column_lineage` keep 1 year.

  — [Databricks: Lineage in Unity Catalog](https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage.html)
- **[SNIPPET/VERIFIED-DOC] Google Dataplex/BigQuery:**
  - "Column-level lineage isn't collected for BigQuery load jobs or for routines" (stored procedures).
  - Routines get no direct lineage. If a routine is used in a query, lineage connects the tables it reads to the query's target.
  - Column lineage is dropped (table-level only) when a job creates more than 1,500 column links. There is no API for column-level lineage.

  — [Google Cloud: About data lineage](https://cloud.google.com/dataplex/docs/about-data-lineage)

### Inferences
- The platform owners, who own the engine and could do anything, chose runtime capture for procedural code. That says static procedural analysis is hard enough that even they don't attempt it. Runtime capture has its own blind spots: only executed paths, only after setup, and temp tables and DDL are often missed.
- PostgreSQL has no Snowflake-style ACCESS_HISTORY. The closest building blocks are `log_statement` and auto_explain-style logging, plus hooks. That is an inference; PostgreSQL mechanisms were not researched here. A hybrid design ("static = possible, runtime = observed") is the only route vendors use to cover dynamic SQL soundly.

### Gaps
- Purview for Synapse and SQL Server, AWS DataZone/Glue lineage, and OCI Data Catalog were not researched in this session.
- No platform publishes accuracy numbers.

---

## 5. Patents disclosing procedural or stored-procedure lineage techniques

### Takeaway
My searches did not surface any patent specifically on stored-procedure or dynamic-SQL column lineage from IBM/Manta, Informatica, Collibra or Microsoft. Only generic lineage patents appeared. This question is essentially unanswered.

### Cited Findings
- US patent 11,086,751, "Intelligent metadata management and data lineage tracing" (assignee not checked). — [USPTO PDF 11086751](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/11086751)
- US patent 12,675,483, "Graph-based techniques for detecting and storing data set dependencies" (assignee not checked). — [USPTO PDF 12675483](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12675483)

### Inferences
- I found no public evidence that Manta patented its approach; its technical disclosure seems to come through academic collaboration (Charles University, CTU) instead.

### Gaps
- A proper Google Patents search was not completed: general web search returned listicles, and the two USPTO PDFs were not read. Recommended follow-up: search Google Patents directly for assignee "Manta Software" / "International Business Machines" + "data lineage" + "stored procedure" / "dynamic SQL", and for Informatica "data lineage" "PL/SQL".

---

## 6. Practitioner write-ups: why procedural lineage is hard

### Takeaway
Practitioners agree on the causes: parsers are built for declarative statements, procedures carry data through variables, collections, branches, loops and temp tables, and dynamic SQL fixes its identifiers only at runtime. The consensus fix is to separate declared (static) from observed (runtime) lineage and to label which one each edge came from.

### Cited Findings
- "Most SQL parsers are built to handle declarative SQL... Stored procedures, on the other hand, contain procedural logic — IF/ELSE branches, loops, exception handlers, cursor operations, dynamic SQL construction, and vendor-specific control flow." Catalogs show procedure code but don't parse it for lineage. **[vendor-authored]** — [Gudu blog p=3177](https://www.dpriver.com/blog/?p=3177)
- Runtime-lineage post (OneUptime, Sept 2026):
  - "Static analysis sees the code that might run. Dynamic SQL chooses the code that did run only after configuration, templates, branches, and stored procedures have resolved."
  - It proposes capture layers ordered by trust: app/orchestrator, then DB-client wrapper around `execute()`, then audit rows written by the stored procedures, then DB statement logs.
  - Each edge is labelled `STATIC`, `RUNTIME_DRIVER` or `DATABASE_LOG`. "Declared" (possible) and "observed" (evidenced) lineage are kept apart, and discrepancies raise alerts.
  - For Postgres it uses `application_name` and `log_line_prefix`, plus a run ID propagated end to end.

  — [OneUptime: Runtime lineage for dynamic SQL](https://oneuptime.com/blog/post/2026-09-07-runtime-lineage-dynamic-sql/view)
- Microsoft makes the same argument: parametrised dynamic SQL and if/then/else script choice "require dynamic analysis". — [Azure blog](https://azure.microsoft.com/blog/introducing-dynamic-lineage-extraction-from-azure-sql-databases-in-azure-purview/)
- Metaplane: real queries are much more complex than documentation examples. A comprehensive test suite was essential, and temp-table update chains were still unsolved. — [Metaplane blog](https://www.metaplane.dev/blog/column-level-lineage-an-adventure-in-sql-parsing)
- Dataedo's ANTLR experience: one unsupported element can stop a whole procedure from parsing. That argues for error-tolerant, per-statement parsing. — [Dataedo PL/SQL parsing](https://docs.dataedo.com/data-sources/sql-parser/plsql-parsing)
- Dataedo also says synonyms whose base objects aren't documented block linkage, and recommends human governance review on top of automatic lineage. — [Dataedo SQL Server lineage](https://dataedo.com/kb/databases/sql-server/automatic-data-lineage-in-sql-server)

### Inferences
- Our plan of one rule per syntactic form is the pattern practitioners describe as brittle. Vendors with the deepest claims (Manta, and GSP by its own account) describe lowering code into an internal data-flow representation and running general analysis over it (symbolic data flow, variable and type resolution).

### Gaps
- Engineering-blog posts from Airbnb, Uber, LinkedIn, Netflix, Pinterest, Grab, Wix and Yelp, and conference talks (PGConf, Data Council, Coalesce, Data+AI Summit), were not retrieved in this session. None of the results retrieved addressed stored-procedure lineage specifically. Datafold and Tobiko/SQLMesh column-lineage posts were also not retrieved.

---

## 7. Published benchmarks, test suites, and coverage metrics for procedural lineage

### Takeaway
No vendor or academic source found publishes a procedural-lineage benchmark or precision/recall numbers. The figures that do exist cover single-statement SQL (DataHub's 97-99%, self-reported) or parse success (Gudu's 14-case comparison). LineageX (ICDE 2025, PostgreSQL-only) gives only qualitative comparisons and leaves procedures out.

### Cited Findings
- DataHub: "97-99% accuracy" (self-reported, methodology not shown), for single-statement SQL only. Procedures and multi-statement scripts are unsupported. — [DataHub SQL parsing](https://docs.datahub.com/docs/lineage/sql_parsing)
- Gudu: GSP 14/14, sqlglot 11/14, JSQLParser 11/14 parse success on 14 patterns across 6 dialects, some of them procedural. This is vendor-authored. — [Gudu comparison](https://www.dpriver.com/blog/gsp-vs-jsqlparser-vs-sqlglot-sql-parser-comparison-2026/)
- LineageX (SFU/Tsinghua, ICDE 2025 demo; open source at github.com/sfu-db/lineagex):
  - It does column lineage for PostgreSQL by AST traversal, reordering queries to resolve ambiguity when metadata is missing.
  - It is evaluated on MIMIC (26 base tables, 70 views, more than 300 columns) with **no numeric precision or recall**, only qualitative comparison with SQLLineage.
  - Procedures and functions are not addressed.

  — [arXiv 2505.23133](https://arxiv.org/html/2505.23133v1); [ICDE 2025 PDF](https://dbgroup.cs.tsinghua.edu.cn/jnwang/papers/icde2025-lineagex.pdf)
- Vendor "supported technologies" matrices are the only systematic coverage statements, and they are at feature level, not measured: [IBM PostgreSQL](https://www.ibm.com/docs/en/DSXDOC/wsj/lineage/lineage-postgresql.html), [Collibra](https://productresources.collibra.com/docs/collibra/latest/Content/CollibraDataLineage/ref_technical-lineage-supported-data-sources.htm), [Dataedo PL/SQL](https://docs.dataedo.com/data-sources/sql-parser/plsql-parsing), [Ataccama Oracle](https://docs.ataccama.com/one/16.1.0/lineage/oracle-lineage-scanner.html), [Alation](https://docs.alation.com/en/latest/sources/Lineage/StoredProcedureLineage.html).

### Inferences
- A blind-corpus recall number for procedural lineage (such as our 77%) is already more than any vendor publishes. Nothing public is comparable, so it can't be benchmarked against the industry directly. If we want comparisons, our own corpus plus GSP or SQLFlow trial runs would be the practical way to get them.

### Gaps
- No "LineageBench" or similar procedural benchmark was found; I did not search arXiv for one exhaustively. I did not inspect Gudu's GitHub test-case repositories.

---

## 8. Lessons we can reuse for a PL/pgSQL-capable, no-LLM analyser

### Takeaway
The industry's deepest static approach (Manta, and GSP by its own account) is a per-language front-end lowering to a common data-flow IR, analysed with general symbolic or data-flow techniques. Everyone else either restricts itself to a statement subset or captures runtime. Every vendor that documents its limits admits dynamic-SQL gaps.

### Cited Findings
- Manta: per-technology scanners produce a data-flow graph through modular symbolic data-flow analysis. — [ICSE 2024 SEIP](https://conf.researchr.org/details/icse-2024/icse-2024-software-engineering-in-practice/28/Data-Lineage-Analysis-for-Enterprise-Applications-by-Manta-The-Story-of-Java-and-C-)
- Manta resolves dynamic SQL "in some cases" through pattern strategies drawn from a study of typical data-warehouse usage. — [CTU thesis](https://dspace.cvut.cz/entities/publication/927f2e83-cbe4-4155-a37e-b1d7bbd34de6)
- GSP resolves variables and %TYPE, collections and SELECT INTO temp-table schemas, and `EXECUTE format()` (self-claimed). — [Gudu p=3177](https://www.dpriver.com/blog/?p=3177); [python_data_lineage](https://github.com/sqlparser/python_data_lineage)
- Dataedo models a procedure as ordered steps and surfaces unsupported statements as explicit named steps. — [Dataedo](https://dataedo.com/kb/databases/sql-server/automatic-data-lineage-in-sql-server)
- DataHub attaches a 0-1 confidence score to each parse. — [DataHub](https://docs.datahub.com/docs/lineage/sql_parsing)
- Runtime-evidence labelling (`STATIC` / `RUNTIME_DRIVER` / `DATABASE_LOG`) is a practitioner pattern. — [OneUptime](https://oneuptime.com/blog/post/2026-09-07-runtime-lineage-dynamic-sql/view)
- Snowflake's parent/root query-ID model shows how to attach executed statements to their calling procedure. — [Snowflake](https://docs.snowflake.com/en/user-guide/access-history)

### Inferences
1. **Replace per-form rules with a lowering step.** Translate the PL/pgSQL AST (pglast exposes `plpgsql` JSON via `parse_plpgsql`) into a small IR: assignments, SQL statements with bound variables, branches, loops, EXECUTE, RETURN QUERY, PERFORM and CALL. Then run a standard dataflow/abstract-interpretation pass over it: reaching definitions, column-provenance sets per variable and record field, and a join at branches. New syntactic forms then only need lowering, not new lineage rules. This mirrors what Manta and GSP describe.
2. **Dynamic SQL: string abstract domain plus explicit unknowns.** Do constant and concatenation propagation through `format()`, `||` and `quote_ident`. If the string resolves to a finite set, parse each candidate. Otherwise emit a typed "unresolved dynamic SQL" boundary, as Dataedo and IBM do, instead of dropping it.
3. **Optional runtime evidence channel.** Accept PostgreSQL statement logs (with `application_name` and run IDs) as a second evidence source and label each edge's provenance. That follows the Purview and Snowflake design and the OneUptime pattern, and it still needs no LLM.
4. **Per-statement failure isolation and confidence scores** (Dataedo, DataHub).
5. **Licensable fallback:** Gudu GSP is the only commercial engine found that claims PL/pgSQL procedural data-flow. It is Java and needs a commercial OEM licence; benchmark it on our holdout before buying.

### Gaps
- None of the vendors disclose IR details publicly beyond the ICSE abstract. The inferences above are architectural extrapolation, not documented vendor internals.
