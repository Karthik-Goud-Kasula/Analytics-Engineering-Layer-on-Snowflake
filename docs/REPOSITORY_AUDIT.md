# Repository audit — October 2026

Audit, repair and completion of
[Analytics-Engineering-Layer-on-Snowflake](https://github.com/Karthik-Goud-Kasula/Analytics-Engineering-Layer-on-Snowflake),
starting from commit `f41a58b` ("Add files via upload", 2026-08-06), the only
substantive commit.

**Environment of this audit:** Python 3.12, dbt Core 1.12.6 with dbt-snowflake
1.12.3 and dbt-duckdb 1.11.0, Apache Airflow 3.3.2 (separate environment). No
Snowflake credentials were available, and sec.gov and hub.getdbt.com were
unreachable from the build environment. Everything below that says *passed* was
executed; everything that could not be executed is listed in
[§18](#18-tests-not-executed-and-why).

**Snowflake verification (2026-10-15):** run by the repository owner against a
Snowflake trial account from a GitHub Codespace (Python 3.14.2, dbt Core 1.12.6,
dbt-snowflake 1.12.3), with results reported back. See
[§21](#21-snowflake-verification-2026-10-15).

---

## 1. Executive summary

The original repository was a well-written **description** of a dbt project, not
a runnable one. Of the 24 models the README listed, 2 existed, and both
referenced models that did not; seeds, macros, tests, ingestion scripts,
Snowflake setup, CI and documentation folders the README linked to did not
exist; the files that did exist sat at the repository root, where neither dbt
nor GitHub Actions would find them. The README's badges and figures ("155
tests", Airflow orchestration, CI against a real warehouse) described things
that were not there. Nothing could run.

The design reasoning in `MODELING.md` and `DECISIONS.md` was strong, so the
work implemented the project those documents describe — honestly scoped — and
corrected the parts of the design that turned out to be wrong:

* the SEC files are **tab**-delimited (not pipe), and since December 2024 the
  numeric-facts grain has **eight** columns (a new `segments` column), not seven;
* the documented incremental watermark (`filed_date`) silently drops backfilled
  data — measured: 349 of 349 facts in a backfilled quarter;
* the documented amendment rule (drop `prevrpt` filings) loses a year of data
  whenever the only amendment is a Part III amendment;
* the existing income-statement model nulled every EPS value and could mix
  figures from different filings in one row;
* two design notes misdescribed dbt and Snowflake behavior (`dbt build`
  ordering; future grants).

**Final state:** 22 models, 2 seeds, 134 data tests, 4 dbt unit tests, 71 Python
tests (66 + 5 Airflow DAG tests), a synthetic sample with known answers, a transactional loader for
Snowflake and DuckDB, least-privilege Snowflake setup scripts, offline CI, and
an opt-in Snowflake integration workflow. `make ci` passes from a clean state.
Seven deliberately re-introduced defects were each caught by the test suite.

**Verdict: end-to-end tested against Snowflake** (updated 2026-10-15). On a live
Snowflake account:
- the sample data loaded through the transactional loader;
- `dbt build` passed twice with `PASS=158 WARN=2 ERROR=0`, the second run
  exercising the incremental merge;
- all five verification queries matched the expected results when run as the
  read-only analyst role.

The run found no defects in models, SQL or the loader. It did find gaps in the
setup instructions, now fixed ([§21](#21-snowflake-verification-2026-10-15)).

The first runs with real SEC data (2026q1) found four problems, all fixed and
tested locally ([§22](#22-first-real-sec-data-attempt-2026-10-15)):
- the loader refused the whole quarter over two quoted rows;
- `dbt_utils.equal_rowcount` errors on empty tables, and passes when only one
  side is empty;
- the SEC's documented fact key is not unique: one 10-K has 17 pairs that share it;
- two F-1 filings have no period date, which the SEC documents as required.

Not yet verified: a completed real-data build, the Snowflake GitHub Actions
workflow, and any production deployment.

---

## 2. Original repository structure

16 files, all at the root, in 2 commits (`43e803d` initial README; `f41a58b`
upload).

| File | Purpose (as documented) | State found |
|---|---|---|
| `README.md` | Project landing page | Well written; most described components did not exist; broken links; wrong clone URL |
| `MODELING.md`, `DECISIONS.md` | Design documentation | Strong reasoning; linked from README as `docs/...` (did not exist); several factual errors (§4–§8) |
| `dbt_project.yml` | dbt config | Valid YAML; `model-paths: ["models"]` but no `models/` folder; config for absent seeds and snapshots; on-run-end hook calling an absent macro |
| `int_facts__income_statement.sql` | Income statement pivot | At root (invisible to dbt); `ref`s absent model and seed; EPS bug; mixed-source rows |
| `fct_financial_ratios.sql` | Ratios | At root; `ref`s absent model; calls absent macro (21×); unnecessary clustering |
| `sec_financials_pipeline.py` | Airflow DAG | At root (not a DAGs folder); Airflow 2 APIs (EOL); functional bugs (§5) |
| `ci.yml` | CI workflow | At root (GitHub only runs `.github/workflows/`); calls absent scripts; needs secrets and an S3 bucket |
| `Makefile` | Developer commands | 5 of 18 targets referenced absent files |
| `packages.yml` | dbt packages | 4 packages, 3 unused; `calogica/dbt_expectations` range from a superseded publisher |
| `profiles.yml.template` | dbt profile | Credentials via env vars (good); had to be copied to `~/.dbt`; no offline target |
| `requirements.txt` | Python deps | dbt 1.8 (now deprecated); no dev/runtime split |
| `.sqlfluff`, `.sqlfluffignore` | SQL lint | Pointed at a root `profiles.yml` that `.gitignore` excludes; ignored `analyses/` |
| `.gitignore` | | Reasonable; would also have excluded any `data/` sample |
| `LICENSE` | MIT, 2026, Karthik Goud Kasula | Correct, kept |

**Referenced but absent:** `models/` (22 of 24 models), `seeds/` (both seeds),
`macros/` (6 described), `tests/` (8 singular + 2 generic tests), `snapshots/`,
`analyses/`, `scripts/load_sec_data.py`, `scripts/check_model_documentation.py`,
`snowflake/setup/*.sql`, `airflow/dags/`, `.github/workflows/`, a deploy
workflow, `docs/`.

**Security:** the full git history was scanned for credential files, private
keys, AWS keys and literal passwords/tokens: **none found**.

## 3. Original architecture

*As documented:* SEC zip → loader → `RAW_SEC` → 4 staging views → 8
intermediate tables → 12 marts, orchestrated by Airflow, with Slim CI against
Snowflake and a production deploy publishing manifests to S3 and docs to GitHub
Pages.

*As implemented:* two SQL files and a DAG file, none of which dbt or Airflow
would load from their locations, and no way to get data into a warehouse.
The documented layering (staging → intermediate → marts) was sound and is the
architecture now implemented ([ARCHITECTURE.md](ARCHITECTURE.md)); the
orchestration and deployment claims were reduced to what exists.

---

Issue fields: **Severity** · **File** · **Component** · *Description* ·
*Root cause* · *Fix* · *Evidence*. Evidence refers to checks in §16–§17.

## 4. Critical bugs

**C1 · Critical · repository layout / `dbt_project.yml`** — *Description:* SQL
files at the root while `model-paths: ["models"]`: dbt finds zero models; the
project cannot run. *Root cause:* files uploaded flat via the GitHub web UI.
*Fix:* standard layout (`models/{staging,intermediate,marts}`, etc.), moved
with `git mv`. *Evidence:* `dbt parse` finds 22 models; `make build`
passes.

**C2 · Critical · `int_facts__income_statement.sql`, `fct_financial_ratios.sql`**
— *Description:* both `ref()` models that did not exist
(`int_facts__classified`, `fct_company_financials_annual`, seed
`financial_tag_mapping`): compilation fails. 22 of 24 documented models were
absent. *Fix:* implemented the 20 missing models needed for a coherent pipeline;
dropped two that added no value (`dim_date`, `dim_financial_tag`); folded
`int_companies__enriched` into `dim_company_history`. *Evidence:* build passes;
every model has tests (`check_project.py`).

**C3 · Critical · `dbt_project.yml` on-run-end, `fct_financial_ratios.sql`** —
*Description:* `on-run-end` called `grant_select_on_schemas`, which did not
exist, so every `dbt run` would fail at the end; `safe_divide` (21 calls) did not
exist. *Fix:* `safe_divide` implemented; the grant hook removed and replaced by
Snowflake future grants (see M9). *Evidence:* build passes.

**C4 · Critical · ingestion** — *Description:* no loader, no setup SQL, no
sample data: no way to put data in a warehouse. *Fix:* `ingestion/` package
(header-validated, transactional, reconciled loads into Snowflake or DuckDB),
`snowflake/setup/01–02`, and a synthetic SEC-format sample. *Evidence:* 50
Python tests; loads verified idempotent.

**C5 · Critical · `ci.yml`** — *Description:* at the root, so GitHub never ran
it; it also called absent scripts, required secrets and an S3 bucket, and its
lint job had no dbt profile. *Fix:* `.github/workflows/ci.yml` (offline, no
secrets) and `snowflake-integration.yml` (manual). *Evidence:* `actionlint`
clean; every CI step executed locally via `make ci`.

## 5. Major functional defects

**H1 · High · `int_facts__income_statement.sql`** — *EPS always NULL.* The
model filtered facts to `unit_of_measure = 'USD'`; EPS is reported in
`USD/shares`. *Fix:* the mapping seed carries the expected unit and the join
matches on it. *Evidence:* unit test `test_income_statement_tag_priority_and_eps_unit`;
e2e; mutation M-4 (§17) re-introduces the bug and the unit test fails.

**H2 · High · `int_facts__income_statement.sql`** — *Rows could mix filings,
comparatives could replace as-reported values, results not deterministic.* The
ranking partitioned by company/period across all filings and ordered by filed
date per concept, so revenue and net income in one row could come from
different filings, a later 10-K's recast prior-year column won over the year's
own 10-K, `max(filing_id)` was reported as the source, and ties had no final
tiebreak. *Fix:* grain is now (filing, duration); only current-period facts
(the 10-K's own year) are used; tag-name tiebreak; `revenue_tag` and
`net_income_tag` record lineage. *Evidence:* ALDER FY2022 = 410,000,000 (not the
recast 414,100,000); reconciliation test `assert_annual_figures_reconcile_to_atomic_facts`;
mutation M-2.

**H3 · High · `MODELING.md` grain; classification** — *Segment rows would
reach company totals.* The SEC added `segments` to `num.txt` in December 2024;
the documented seven-column grain omits it, so segment breakdowns compete with
(or sum into) consolidated figures. *Fix:* eight-column `fact_key`;
classification keeps only consolidated facts (no segments, no co-registrant).
*Evidence:* CEDAR scenario (segment + co-registrant rows) in e2e; mutation M-1.

**H4 · High · `DECISIONS.md` #2 (incremental design)** — *Backfills silently
dropped.* A `filed_date` watermark skips every filing older than the newest one
loaded, which is all of a backfilled quarter. *Fix:* watermark on `loaded_at`
with a lookback; reconciliation tests against staging. *Evidence:*
`reconcile_incremental.py` reports 349/349 facts would have been skipped;
mutation M-7 (partition watermark) fails two reconciliation tests at the
backfill step.

**H5 · High · `MODELING.md` amendment rule** — *A year of data lost when the
only amendment is Part III.* Dropping `prevrpt = 1` filings and taking the
latest filing lets a statement-free 10-K/A win. *Fix:* only filings with primary
financial statements compete; `prevrpt` not used. *Evidence:* unit test
`test_dedup_ignores_amendment_without_statements`; CEDAR in e2e; mutation M-3.

**H6 · High · `sec_financials_pipeline.py`** — *Several functional defects:*
(a) targeted the quarter **two** back — on 2026-10-14 the SEC already lists
`2026q3`, the DAG would load `2026q2` until January; (b) treated every HTTP
error, including 403 (missing/rejected User-Agent), as "not published" →
misconfiguration skipped silently forever; (c) the availability task read
`SEC_USER_AGENT` from the worker environment, which the DAG never set → failure
every run unless set globally; (d) `Variable.get()` at import time (metadata-DB
access on every parse — it errored when tested); (e) called nonexistent scripts.
Under Airflow 3.3.2 it imported with 6 deprecated-API warnings (Airflow 2 reached
end of life on 2026-04-22). *Fix:* rewritten on `airflow.sdk`; the loader owns
quarter logic (`--quarters latest --skip-if-loaded`), only 404 skips (exit 99),
Variables read via run-time templates. *Evidence:* 5 DAG tests pass on Airflow
3.3.2 with zero deprecation warnings; mutating one import back makes the test
fail.

**H7 · High · `README.md`** — *Misrepresented the project* (24 models, "155
tests" badge, Airflow badge, CI against Snowflake, deploy workflow). For a
portfolio, this is the most damaging defect: a reviewer opening `models/` would
find nothing. *Fix:* README rewritten to describe exactly what exists, with a
verification-status note and real counts. *Evidence:* counts taken from
`manifest.json`/pytest; links checked (§16).

**H8 · High · `requirements.txt`, `packages.yml`** — dbt Core 1.8 is listed as
deprecated by dbt Labs; three of four packages were unused. *Fix:* dbt Core
1.12.6 (active support to July 2027) with matching adapters; only dbt_utils
1.4.1, pinned by tag with a lock file. *Evidence:* parse with zero deprecation
warnings; `--use-v2-parser` check passes.

## 6. Minor defects

| # | Sev. | File | Description | Fix |
|---|---|---|---|---|
| M1 | Medium | `README.md` | Said the SEC files are pipe-delimited; they are tab-delimited (a loader built from it would fail) | Corrected; loader uses tabs |
| M2 | Medium | `.sqlfluff` | dbt templater pointed at a root `profiles.yml` that is git-ignored → lint could never run in CI | `profiles/` dir with a `local` target; lint passes |
| M3 | Medium | `profiles.yml.template` | Manual copy to `~/.dbt`; no credential-free target | Committed `profiles/profiles.yml` (env vars only, checked by `check_project.py`) with `local`, `dev`, `ci` |
| M4 | Medium | `dbt_project.yml` | SIC seed typed `varchar(4)` → string `BETWEEN` mis-sorts codes | Integers |
| M5 | Medium | `dbt_project.yml` | Snapshot config with `strategy: check` but no `check_cols` (any snapshot would error); `invalidate_hard_deletes` deprecated; `tests:` key renamed `data_tests:`; comment contradicting the setting; project-wide `store_failures` | Removed/cleaned; `store_failures` only on warn-level domain tests |
| M6 | Medium | `DECISIONS.md` #7 | $1,000 absolute balance-sheet tolerance fails large filers that round to millions | Relative 0.5% of assets |
| M7 | Medium | `Makefile` | Targets referenced absent scripts/files; `.PHONY` incomplete | Rewritten; every target exercised by `make ci` or by hand |
| M8 | Medium | `README.md` | Clone URL of another repo; links to absent `docs/` files; promised `localhost:8080` docs from absent setup | Fixed; links verified |
| M9 | Medium | `DECISIONS.md` #14 | Claimed future grants don't apply to recreated objects; dbt's `CREATE OR REPLACE` creates new objects, to which future grants apply | Future grants in setup SQL; decision rewritten with source |
| M10 | Medium | `DECISIONS.md` #11 | Claimed `dbt build` can build marts while staging tests run; dbt documents that failing upstream tests skip downstream models | Decision corrected |
| M11 | Medium | docs | "68-row seed", "65 SIC ranges", "155 tests" — not backed by any file | Real counts: 63 mappings (62 tags, 33 concepts), 83 SIC major groups, 132 data tests |
| L1 | Low | `fct_financial_ratios.sql` | `cluster_by` on a table of a few thousand rows/year: clustering credits, no benefit | Removed |
| L2 | Low | `fct_financial_ratios.sql` | `cash_conversion_ratio` had the sign problem the doc's own principle guards against | Guarded (`net_income > 0`) |
| L3 | Low | `.sqlfluffignore` | Silently excluded `analyses/` | Removed; analyses linted |
| L4 | Low | `.gitignore` | `data/` rule would exclude sample data; `profiles.yml` rule matched any path | Narrowed to `data/raw/`, `data/warehouse/`, `/profiles.yml` |
| L5 | Low | DAG | `email_on_failure` to `example.com` (no SMTP; deprecated in Airflow 3) | Removed |

## 7. Security issues

No secrets were found anywhere in the history (§2). Improvements made:

| Area | Before | After |
|---|---|---|
| Snowflake privileges | README said to run setup as `ACCOUNTADMIN` (scripts absent) | Split across `USERADMIN`/`SYSADMIN`/`SECURITYADMIN`; three roles with disjoint duties; `ACCOUNTADMIN` only for an optional resource monitor |
| Authentication | Key-pair in template | Key-pair only (Snowflake is blocking password-only sign-in for service users, Aug–Oct 2026); CI uses a `TYPE = SERVICE` user |
| Credential handling | Template copied by hand | Env-var-only profile, enforced by `check_project.py`; `.env` git-ignored; `.env.example` with placeholders only |
| Destructive operations | CI cleanup macro absent | `drop_ci_schemas` refuses to run outside target `ci` and only drops `ci_<run id>` schemas; teardown script fully commented out |
| Input handling | — | Quarter strings validated before being inlined in SQL (tested with an injection attempt); zip members extracted by known name only (path traversal tested) |
| CI | Needed AWS + Snowflake secrets on every PR | PR checks need no secrets (`permissions: contents: read`) |

## 8. Data modeling issues

Covered in H2–H5. Additional decisions: SCD2 implemented as a model derived
from filing history rather than a dbt snapshot (business-dated, reproducible,
backfill-safe; [DECISIONS #16](DECISIONS.md#16-company-history-is-derived-from-filings-not-snapshotted));
point-in-time sector in the annual fact; annual grain made explicit
(company, fiscal year) with a deterministic rule for fiscal-year-end changes.

## 9. Data quality gaps

Original: no tests existed. Now: 134 data tests (grain, keys, relationships,
accepted values, domain rules, reconciliations, coverage) and 4 unit tests; each
domain test exists because of a specific failure mode (see README > Data quality
and testing). Severity is deliberate: domain checks that real filers can
legitimately fail warn and store their failing rows; structural checks error
and block downstream models.

## 10. Performance concerns

Unnecessary clustering removed (L1). Single incremental model with a
load-time filter. X-Small warehouse, 60 s auto-suspend, statement timeout,
optional resource monitor, query tags. Transient tables (dbt-snowflake default).
See README > Performance and cost.

## 11. Documentation gaps

Absent before: setup instructions that worked, Snowflake setup, data dictionary,
expected outputs, troubleshooting, architecture diagrams of what exists. All
added; the data dictionary is generated from the model YAML and CI fails if it
is stale or if documented columns drift from built tables.

---

## 12. Changes implemented

By phase: **(1)** layout, configuration, dependency versions, the two existing
models fixed; **(2)** ingestion, sample data, Snowflake setup, 20 new models,
seeds, macros, tests; **(3)** CI, Makefile, lint configuration, Airflow 3 DAG,
validation scripts; **(4)** README and documentation.

## 13. Files added

| Path | Purpose |
|---|---|
| `ingestion/{sec_layout,quarters,sources,warehouses,load_sec_data,sample_data}.py` | Loader and sample generator |
| `data/sample/<6 quarters>/{sub,num,tag,pre}.txt`, `data/sample/README.md` | Synthetic sample (24 files) |
| `snowflake/setup/{01_account_setup,02_raw_tables,99_teardown}.sql`, `snowflake/queries/verify_outputs.sql` | Snowflake setup and verification |
| `models/staging/sec/*` (4 SQL + 2 YAML) | Staging |
| `models/intermediate/*` (6 new SQL + YAML with 3 unit tests) | Intermediate |
| `models/marts/core/*` (4 SQL + YAML with 1 unit test) | Dimensions, SCD2, incremental fact |
| `models/marts/finance/*` (6 new SQL + YAML) | Finance marts |
| `macros/{safe_divide,parse_yyyymmdd,statement_helpers,drop_ci_schemas}.sql` | Macros |
| `seeds/{financial_tag_mapping,sic_sector_mapping}.csv`, `seeds/_seeds.yml` | Seeds |
| `tests/*.sql` (7), `tests/generic/not_null_proportion.sql` | Singular and generic tests |
| `analyses/*` (3 queries + YAML) | Example analyses |
| `python_tests/*` (8 files) | Python tests |
| `scripts/{check_project,build_data_dictionary,reconcile_incremental,check_snowflake_sql}.py` | Validation |
| `.github/workflows/snowflake-integration.yml` | Manual Snowflake workflow |
| `docs/{ARCHITECTURE,DATA_DICTIONARY,EXPECTED_OUTPUTS,TROUBLESHOOTING,REPOSITORY_AUDIT}.md` | Documentation |
| `requirements-dev.txt`, `pyproject.toml`, `.env.example`, `package-lock.yml`, `.gitattributes` | Tooling |

## 14. Files modified

Moved with `git mv` and then edited (git links history across a move only when the
content stays similar — here `fct_financial_ratios.sql`; the others were largely rewritten): `ci.yml` →
`.github/workflows/ci.yml` (rewritten), `MODELING.md`/`DECISIONS.md` → `docs/`
(corrected, original reasoning kept where right), `int_facts__income_statement.sql`
and `fct_financial_ratios.sql` → `models/...` (fixed, original structure and
comments kept), `sec_financials_pipeline.py` → `orchestration/airflow/dags/`
(Airflow 3), `profiles.yml.template` → `profiles/profiles.yml`.
Edited in place: `README.md`, `dbt_project.yml`, `packages.yml`,
`requirements.txt`, `Makefile`, `.gitignore`, `.sqlfluff`, `.sqlfluffignore`.
Unchanged: `LICENSE`.

## 15. Files removed

None deleted outright. `profiles.yml.template` became `profiles/profiles.yml`
(same purpose, now usable directly). Removed *references*, with reasons: three
unused dbt packages; the S3/Slim-CI and deploy steps (no production environment
exists); the `generate_schema_name`, `limit_data_in_dev` and grant macros (not
needed — DECISIONS #13–#14; dev volume is set by which quarters you load);
snapshot configuration (SCD2 is a model).

---

## 16. Tests executed

All run on 2026-10-14 in the environment described at the top.

| # | Check | Command |
|---|---|---|
| 1 | Python lint + format | `ruff check`, `ruff format --check` |
| 2 | Python unit tests | `pytest` (loader, layouts, Snowflake loader logic with a fake connection, SEC download with simulated responses, sample data) |
| 3 | Sample data reproducible | `python -m ingestion.sample_data --check` |
| 4 | dbt deps | `dbt deps` (dbt_utils 1.4.1 from GitHub) |
| 5 | Sample load into DuckDB | `make load-sample` |
| 6 | dbt build | `dbt build --target local` (seeds, 22 models, 132 data tests, 4 unit tests) |
| 7 | End-to-end oracle | `pytest python_tests/test_expected_outputs.py` |
| 8 | Docs and project rules | `dbt docs generate`, `check_project.py --catalog`, `build_data_dictionary.py --check` |
| 9 | SQL lint | `sqlfluff lint models tests analyses` (Snowflake dialect, dbt templater) |
| 10 | Snowflake SQL, offline | `check_snowflake_sql.py`: compile with dbt-snowflake (unreachable account, both incremental branches), parse with sqlfluff + sqlglot Snowflake grammars |
| 11 | Incremental ≡ full refresh | `reconcile_incremental.py` |
| 12 | Full CI pipeline from clean state | `make ci` (2 runs; see §17) |
| 13 | Airflow DAG | `pytest python_tests/test_airflow_dag.py` on Airflow 3.3.2 |
| 14 | Original DAG under Airflow 3.3.2 | DagBag import of the unmodified file |
| 15 | GitHub Actions syntax | `actionlint` 1.7.12 |
| 16 | dbt v2 parser compatibility | `dbt parse --use-v2-parser` |
| 17 | Deprecations | `dbt parse --no-partial-parse` scanned for warnings |
| 18 | Secret scan | git history scan (§2) |
| 19 | Validator sanity | injected syntax errors into SQL; injected a deprecated import into the DAG |
| 20 | Mutation tests | 7 defects re-introduced one at a time in scratch copies (§17) |

## 17. Test results

| Check | Result | Evidence |
|---|---|---|
| ruff | **PASS** | "All checks passed"; 21 files formatted |
| Python unit tests | **PASS** | 42 passed, 1 skipped (DAG test, needs Airflow) |
| Sample reproducible | **PASS** | 24 files up to date |
| dbt build (local) | **PASS** | `PASS=158 WARN=2 ERROR=0 SKIP=0 TOTAL=160`; the 2 warnings are designed (EXPECTED_OUTPUTS.md) |
| End-to-end oracle | **PASS** | 8 passed |
| Docs / project rules | **PASS** | 22 models documented and tested; documented columns = built columns; dictionary current |
| sqlfluff | **PASS** | no violations |
| Snowflake SQL (offline) | **PASS** | 157 nodes × 2 incremental modes + 4 setup scripts parse in both grammars; validators confirmed to catch injected errors |
| Incremental ≡ full refresh | **PASS** | 11/11 mart tables identical; no-op rerun and duplicate re-delivery change nothing; 349 backfilled facts that a filed-date watermark would miss are present |
| `make ci` from clean | **PASS** (2nd run) | First run exposed a real bug: dbt reads `DBT_TARGET` as `--target`, so the offline Snowflake check inherited `local` under `make`. Fixed; second run exit 0 in 3 min 0 s |
| Airflow DAG (new) | **PASS** | 5 passed on Airflow 3.3.2, zero deprecation warnings |
| Airflow DAG (original) | loads, with problems | 6 deprecated-API warnings, email deprecations, `Variable.get` at import errored against the metadata DB |
| actionlint | **PASS** | no problems |
| v2 parser | **PASS** | dbt-oss 2.0.8 parser: parse successful |
| Deprecations | **PASS** | none |
| Secret scan | **PASS** | nothing found |

**Mutation tests** — each defect re-introduced alone; the suite must fail:

| # | Re-introduced defect | Caught by |
|---|---|---|
| M-1 | Drop the consolidated filter (segment/co-registrant rows leak) | `assert_annual_figures_reconcile_to_atomic_facts` + 3 e2e tests |
| M-2 | Drop the current-period filter (comparatives leak) | 4 grain/uniqueness tests; 53 downstream nodes skipped |
| M-3 | Let a statement-free amendment win dedup | dedup unit test; 91 downstream nodes skipped |
| M-4 | Original EPS bug (`unit = 'USD'`) | income-statement unit test |
| M-5 | Derive Q4 from two quarters | Q4 unit test + 1 e2e test |
| M-6 | Naive SCD2 grouping (A→B→A merged) | gaps-and-islands unit test |
| M-7 | "Latest partition" incremental watermark | at the backfill step: row-count reconciliation (then `equal_rowcount`, now `row_count_matches`) + annual-vs-atomic reconciliation |

## 18. Tests not executed, and why

| Check | Status | Why | How to run it |
|---|---|---|---|
| Execution on Snowflake | **Done 2026-10-15**: see [§21](#21-snowflake-verification-2026-10-15) | Was not possible from the audit environment (no credentials); run by the repository owner | — |
| `snowflake-integration.yml` | **NOT RUN** | Needs repository secrets (`SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY`) | Actions tab → *Snowflake integration* → Run workflow |
| Loading real SEC data | **IN PROGRESS** | First attempt (2026q1, by the owner) downloaded and validated the files, then hit two defects, now fixed ([§22](#22-first-real-sec-data-attempt-2026-10-15)); re-run pending | README > Running on Snowflake > Real SEC data |
| GitHub-hosted CI | **Passed (owner-confirmed)** | `ci.yml` runs on pull requests and on `main`. The owner reported green checks on the Actions tab after the merges; this session's network could not read Actions results itself | Repository → Actions tab, or the CI badge on the README |
| `dbt deps` from hub.getdbt.com | n/a | Packages now install from GitHub (verified) | — |
| dbt v2 build | **NOT RUN** | Only the v2 *parser* check was available offline | `pip install dbt` in a separate env, `dbt build --target local` |

## 19. Remaining limitations

See README > Known limitations. In short: verified on Snowflake with synthetic
data only, so real-data coverage of the tag mapping is untested; US-GAAP 10-K/10-Q scope;
as-reported (not recast) figures; derived Q4 absorbs restatements; no quarterly
cash flow; year-end-balance ratios; coarse sectors; source deletions need a full
refresh of the fact table; the Airflow DAG is a reference, not deployed.

## 20. Recommended future improvements

1. Load two or three real quarters, review `mart_data_quality_summary`, and
   extend `financial_tag_mapping.csv` where coverage is low. Now the most
   valuable next step.
2. Add the repository secrets and run the Snowflake integration workflow.
3. Slim CI (`state:modified+ --defer`) once a production environment exists.
4. Test-result history / anomaly detection; model contracts on finance marts;
   dbt exposures for downstream dashboards.
5. Quarterly cash flow from year-to-date differencing; average-balance ratios.
6. Build and test on dbt v2.

## 21. Snowflake verification (2026-10-15)

**Environment:** Snowflake trial account, role ACCOUNTADMIN for setup.
Run from a GitHub Codespace: Python 3.14.2, dbt Core 1.12.6, dbt-snowflake 1.12.3.
Key-pair authentication.

| Step | Result |
|---|---|
| Setup (`00_setup_all_in_one.sql`) | Completed: roles, warehouse, database, raw layer, future grants, CI service user |
| `dbt debug --target dev` | All checks passed |
| `make load-sample TARGET=dev` | 6 quarters loaded via PUT → COPY in one transaction each; row counts reconciled |
| `make build TARGET=dev` (1st) | `PASS=158 WARN=2 ERROR=0 SKIP=0 TOTAL=160`, identical to the DuckDB build; the 2 warnings are the designed ones |
| `make build TARGET=dev` (2nd, incremental merge) | `PASS=158 WARN=2 ERROR=0 SKIP=0 TOTAL=160` |
| `verify_outputs.sql`, 5 queries as `SEC_ANALYST` | All 5 matched `EXPECTED_OUTPUTS.md`, which also confirms the analyst's future grants |

**Code defects found by the Snowflake run: none.** Every model, test and loader
path that had only been compiled and parsed offline ran correctly.

**Setup and documentation problems found, and fixed in this update:**

| # | Problem | Fix |
|---|---|---|
| S1 | The Snowsight editor ran only the statement under the cursor. The multi-statement setup scripts silently ran one line, which surfaced as misleading "does not exist" errors | New `snowflake/setup/00_setup_all_in_one.sql`: the setup as one Snowflake Scripting block. README now points to it; 01/02 kept, with a "Run All" warning |
| S2 | `No active warehouse selected`: a new worksheet has no warehouse | `00_...sql` creates and selects the warehouse first; troubleshooting entry |
| S3 | User **name** ≠ **login name**: an account created with an email address has the email as login name, so `SNOWFLAKE_USER` set to the user name gave `JWT token is invalid`. Lowercase user names also don't match unquoted SQL | `.env.example` explains login name and how to query it; troubleshooting covers name vs login name and a fingerprint check. The setup uses `current_user()` and `alter user set ...` (no name) so users never type their name |
| S4 | `.env` kept the example key path, giving `No such file or directory` | Neutral placeholder plus a command that prints the correct absolute path; troubleshooting entry |
| S5 | Verification queries depended on a `use database` statement | `verify_outputs.sql` is fully qualified and starts with `use role sec_analyst` |
| S6 | Connection check needed `DBT_PROFILES_DIR` set by hand | New `make debug` target |

**Files in this update:** added `snowflake/setup/00_setup_all_in_one.sql`.
Modified:
- `README.md` (status, Running on Snowflake, limitations)
- `docs/TROUBLESHOOTING.md`
- `docs/REPOSITORY_AUDIT.md`
- `docs/EXPECTED_OUTPUTS.md`
- `snowflake/queries/verify_outputs.sql`
- `.env.example`
- `Makefile`
- `python_tests/test_sec_layout.py` (the new script's raw tables are checked against the loader layout)

## 22. First real SEC data attempt (2026-10-15)

**Run by the owner** in the same Codespace and Snowflake account as §21. The raw
tables were emptied, then:
`make load-sec TARGET=dev QUARTERS=2026q1` and `make fresh TARGET=dev`.

| Step | Result |
|---|---|
| Download from sec.gov with `SEC_USER_AGENT` | OK (2026q1.zip, extracted) |
| Header checks, all four files | OK |
| Row checks | `sub.txt`, `tag.txt`, `pre.txt`: 0 malformed rows. `num.txt`: 2 rows with 11 fields instead of 10, so **the loader refused the quarter** and nothing was loaded |
| `make fresh` (run anyway, on empty raw tables) | `ERROR=4`: every `dbt_utils.equal_rowcount` test failed with `None is not of type 'integer'`; 17 nodes skipped |

**R1 — quoted values (loader).** Both `num.txt` rows (lines 2,451,014 and
2,535,977, accession `0001628280-26-020991`) have a typed-dimension label in
`segments` that contains a tab. The SEC encloses that value in double quotes:
`"InvestmentIdentifier=Unfunded equity<TAB>securities;"`. Read without quoting,
which the loader did on purpose (see DECISIONS #17), the value splits in two, and
`value` and `footnote` shift one column right. The loader was right not to load
these rows as they were. It was wrong to refuse the whole quarter over two rows
out of millions.
*Fix:* `sec_layout.check_rows()` re-reads only malformed rows with quote handling
on. It accepts the result only when it has exactly the header's field count and
passes per-file checks (`num.value` numeric, `ddate` eight digits, `qtrs`
numeric, and similar for the other files). The tab becomes a space. Any other
malformed row is left out and written with its line number to
`<file>.rejected.txt`. More than `--max-rejected-rows` (default 100) per file
still fails the load. Well-formed rows are never re-read, so a leading quote in a
company name stays data.

**R2 — empty reconciliations (dbt).** `dbt_utils.equal_rowcount` (1.4.1) counts
with `GROUP BY` on a constant, and that returns no row for an empty table. Both
sides empty means a NULL failure count, which is the error seen. Investigating
it showed a worse case: **one side empty passes**, because the NULL difference is
coalesced to 0. Reproduced locally: one row in `dim_filing` against an empty
staging model gave PASS, with the package default and with a `fail_calc`
override alike.
*Fix:* `tests/generic/row_count_matches.sql` (plain aggregates, always one row)
replaces all five uses. Locally verified: both empty → PASS, 1 vs 0 → FAIL 1,
equal → PASS.

**R3 — building on nothing (dbt).** On empty raw tables every other test passes
on zero rows. *Fix:* `assert_raw_sec_data_is_loaded` (`dbt_utils.at_least_one`
on `sub.adsh`) fails first and skips everything downstream (verified on an empty
DuckDB warehouse: `FAIL 1`, 125 nodes skipped).

**Second run (after the fixes above).** The load succeeded, including both
repaired rows. `make fresh` gave `PASS=45 ERROR=2 SKIP=114`: two staging tests
failed, and everything downstream of staging was skipped. Profiling the raw
tables in Snowsight found:

**R4 — the documented fact key is not unique (data).**
`unique_stg_sec__numeric_facts_fact_key` failed with 17 duplicates, all in one
10-K (`0001918712-26-000018`). They are derivative contracts whose
typed-dimension identifier the SEC published as identical text
(`OpenForwardForeignCurrencyContractIdentifier=, Wells Fargo B...`). The values
differ in 16 of the 17 pairs, so these are different facts, not repeated rows.
*Fix:* staging keeps every row. `grain_ordinal` (`row_number()` over the eight
columns, ordered by value, then footnote) goes into `fact_key` only for the
second and later rows, so existing keys and incremental tables are unaffected. A
warn-level `accepted_values` test on `grain_ordinal` reports such rows on every
build. Reproduced and verified locally with an injected duplicate: both rows are
kept (2,117 facts, 2,117 distinct keys), and the build passes with a warning.

**R5 — `period` missing (data).** `not_null_stg_sec__submissions_period_end_date`
failed for 2 rows: an F-1 and an F-1/A with an empty `period`, which the SEC
documents as never empty. The check that filings are not filed before their
period end held for every form (0 violations).
*Fix:* the not-null test applies only to 10-K and 10-Q filings, the forms the
marts use. Verified locally: an F-1 without a period passes; a 10-K without one
still fails.

**Verification of the fixes (local, offline):** `make ci` from a clean warehouse
exited 0. Python tests: 66 passed, including the exact 2026q1 row as a fixture.
`dbt build`: `PASS=160 WARN=2 ERROR=0 TOTAL=162`. End-to-end oracle 8/8.
Incremental ≡ full refresh 11/11 tables. Docs check, sqlfluff and the offline
Snowflake compile all passed. The row check costs about 10 s for a synthetic
3.5-million-row `num.txt` with one repaired row, including writing the cleaned
copy.

**Not yet verified:** a complete real-data build on Snowflake with these fixes.
Results go here when reported.

**Files:** added `tests/generic/row_count_matches.sql`. Modified:
- `ingestion/sec_layout.py`, `ingestion/load_sec_data.py`
- `models/staging/sec/_sec__sources.yml`, `_sec__models.yml`, `stg_sec__numeric_facts.sql`
- `models/marts/core/_core__models.yml`, `models/marts/finance/_finance__models.yml`
- `models/marts/core/fct_financial_facts.sql` (comment)
- `python_tests/test_sec_layout.py`, `python_tests/test_loader_duckdb.py`
- `README.md`
- `docs/DECISIONS.md` (#17, new #21 and #22)
- `docs/TROUBLESHOOTING.md`, `docs/EXPECTED_OUTPUTS.md`, `docs/MODELING.md`, `docs/DATA_DICTIONARY.md` (regenerated), `docs/REPOSITORY_AUDIT.md`
