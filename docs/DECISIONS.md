# Design Decisions

Each entry records the choice, what was rejected, and the trade-off accepted. Written for the version of me who returns in six months and wonders why something is the way it is.

Entries marked **Revised** changed during the October 2026 audit ([REPOSITORY_AUDIT.md](REPOSITORY_AUDIT.md)); the original reasoning is kept where it was right, and corrected where it was not.

---

## 1. Intermediate models materialize as tables

**Rejected:** ephemeral.

**Why:** ephemeral models are inlined as CTEs into every consumer. `int_facts__classified` has three consumers and `int_filings__deduplicated` four, so ephemeral means the warehouse derives the same result several times per build.

**Trade-off:** more storage, and intermediate tables show up in the warehouse where a curious analyst might query them directly.

---

## 2. Only the atomic fact table is incremental

**Rejected:** incremental everywhere.

**Why:** `fct_financial_facts` is the only model where a full refresh is genuinely expensive — tens of millions of rows across the full SEC history. Everything else rebuilds in seconds to low minutes.

Incremental models carry real cost: state to reason about, a `unique_key` that must actually be unique, an `is_incremental()` branch that behaves differently from the full-refresh path, and a whole class of "why is this row stale" bugs. Paying that to save 40 seconds on a dimension table is a bad trade.

**Revised — the watermark is load time, not filed date.** The original design filtered on `filed_date` with a 7-day overlap. That breaks on the most ordinary operational event, a backfill: loading `2023q1` after `2024q2` brings in filings whose `filed_date` is a year older than anything in the table, and a filed-date filter skips every one of them. `make incremental-check` measures it on the sample: **349 of 349** backfilled facts would have been missed. The model now filters on `loaded_at`, which the loader stamps and which only moves forward; a 24-hour lookback absorbs loads that commit out of order, and the merge on `fact_key` makes reprocessing the overlap a no-op.

**Also:** the table stores only immutable attributes (mutable ones live in the dimensions), and an `equal_rowcount` test against staging detects rows left behind by source deletions — the one thing incremental logic can't see. The fix is a `--full-refresh` of that model.

---

## 3. `safe_divide()` everywhere

**Rejected:** raw `/`, and `nullif()` written inline at each site.

**Why:** Snowflake raises on `x / 0` and returns NULL on `x / NULL`. Neither is right for an analytical model. A ratio with a zero denominator is *undefined*, and NULL is the honest representation.

Writing `nullif()` inline at each of the 29 division sites means 29 chances to forget. A macro means one place to get it right, and one place to change the behaviour if the team later decides zero should yield zero. It also casts both sides to double, so ratios are identical on Snowflake and DuckDB.

**Trade-off:** slightly noisier SQL, and `dbt compile` output is harder to eyeball.

---

## 4. Ratios are NULLed where the sign makes them meaningless

**Rejected:** computing them anyway and letting the analyst filter.

**Why:** a company with negative equity and negative net income produces a *positive* ROE. That number is not just imprecise — it's actively misleading, and it will end up on a dashboard sorted descending, putting the most distressed companies at the top of a "best performers" list.

`case when total_equity > 0 then ... end` costs one line and prevents that. **Revised:** the same guard now applies to `cash_conversion_ratio` (operating cash flow ÷ net income), which had the identical sign problem.

**Trade-off:** more NULLs to explain. Documented per column in MODELING.md.

---

## 5. Sector mapping is a range seed of SIC major groups

**Rejected:** a seed with one row per four-digit SIC code. **Also rejected:** a `CASE` statement in the model.

**Why:** SIC is hierarchical and range-based, so ranges are the honest representation. The seed holds the 83 major groups of the SIC Manual (e.g. 7300–7399 *Business Services*) with their division as `sector`. A `CASE` statement would bury business logic in SQL where a non-engineer can't update it.

**Revised:** the codes are integers. The original config typed them `varchar(4)`, but SEC SIC codes have no leading zeros, and a string `BETWEEN` sorts `'730'` after `'7299'`.

**The catch:** a `BETWEEN` join fans out if ranges overlap. One overlapping range silently duplicates every company in the overlap, and duplicate rows in a company dimension fan out every downstream join — producing inflated totals with no error anywhere. `assert_sic_ranges_do_not_overlap.sql` is what makes the range join safe. The seed and the test are a package; neither is correct without the other.

---

## 6. Amendments are kept, not discarded at ingestion

**Rejected:** filtering `form LIKE '%/A'` at the staging layer.

**Why:** discarding them at ingestion is simpler and makes every downstream model cleaner. It also makes restatement analysis permanently impossible, and a material restatement is one of the more interesting signals in financial data.

The cost of keeping them is contained: exactly one model (`int_filings__deduplicated`) handles resolution, and exactly one model (`mart_restatements`) deliberately bypasses it.

**Revised:** resolution now considers only filings that contain financial statements, and does not use the SEC's `prevrpt` flag — see MODELING.md > Amendment handling for the Part III amendment trap.

---

## 7. `assert_balance_sheet_balances` warns instead of erroring

**Rejected:** `severity: error`.

**Why:** a small number of companies legitimately fail A = L + E as modeled here — redeemable preferred classified as temporary equity, non-controlling interests outside parent equity, and filers who simply don't tag a subtotal. Blocking every build on those teaches the team to bypass the test, which is worse than not having it.

The test escalates to an error past 2,000 failures (`error_if: '>2000'`). At that volume the mapping is broken, not the filers. Failing rows are stored for inspection.

**Revised:** the tolerance is relative (0.5% of total assets) instead of an absolute $1,000. Filers round each line item to thousands or millions, so subtotal rounding alone can exceed $1,000 for every large company.

**This is the general principle for test severity here:** a test that fires constantly gets ignored, and an ignored test is worse than no test because it creates false confidence.

---

## 8. Medians, not means, in sector benchmarks

**Rejected:** `avg()`.

**Why:** financial ratios are heavily right-skewed. One company with a near-zero equity base and a 400x return ratio drags a sector mean somewhere useless. An analyst asking "is a 22% operating margin good for software?" wants the median and the interquartile range.

Sector-years with fewer than 5 companies are excluded entirely. A "median" over three companies isn't a benchmark, and publishing it invites misuse.

---

## 9. CI is offline by default; Snowflake runs are opt-in

**Revised.** The original design described Slim CI (`state:modified+ --defer`) against a production manifest downloaded from S3 on every pull request. None of it existed: there was no production environment, manifest, bucket, or deploy workflow — and the workflow file sat at the repository root, where GitHub never runs it.

**Now:** every pull request runs the whole project offline — lint, Python tests, `dbt build` with all tests on a DuckDB file loaded with the sample data, an end-to-end comparison against expected results, an incremental-vs-full-refresh reconciliation, and an offline compile-and-parse of every model as Snowflake SQL. No secrets, so it also works for pull requests from forks. A separate, manually triggered workflow builds against a real Snowflake account when secrets are configured.

**Rejected:** making pull requests depend on Snowflake credentials. A portfolio project's CI should be green or red because of the code, not because a trial account expired.

**Trade-off:** offline CI cannot catch Snowflake *runtime* errors (a function that parses but behaves differently, a missing privilege). That is what the integration workflow is for. Slim CI becomes worthwhile once there is a production environment whose manifest can be the baseline.

---

## 10. The scheduled load polls weekly and asks the loader what is new

**Rejected:** a quarterly schedule aligned to SEC publication. **Revised:** the original monthly schedule.

**Why:** the SEC posts each quarterly file shortly after the quarter closes but commits to no date. A run that finds nothing costs one HTTP request and ends as *skipped*, so polling weekly is cheap and bounds staleness at about a week; a monthly poll could leave a published quarter unloaded for a month.

**Revised — target quarter.** The original DAG targeted the quarter *two* back from the current one, assuming a six-week publication lag. As of 14 October 2026 the SEC already lists `2026q3`, two weeks after it closed; two-back would have kept loading `2026q2` until January. The loader now targets the latest completed quarter, and `--skip-if-loaded` makes reruns free.

**Missing data is a skip, not a failure** — unchanged. The loader exits 99 when the quarter isn't published (HTTP 404) or is already loaded, which Airflow's `BashOperator` treats as skipped, so a red DAG still means something. **Revised:** the original skipped on *any* HTTP error, including 403 — which is what the SEC returns for a missing or rejected User-Agent. A misconfiguration would have looked like "not published yet" forever. Only 404 skips now.

---

## 11. Staging is tested before intermediate runs

**Revised — the original premise was wrong.** The original said `dbt build` "runs tests as soon as each model's dependencies are met, which means marts can start building while staging tests are still running." It doesn't: [dbt's documentation](https://docs.getdbt.com/reference/commands/build) is explicit that a test failure on an upstream model causes its downstream models to be *skipped*. `dbt build` already guarantees nothing is built on data that failed validation.

**Now:** the DAG still runs `dbt build` one layer at a time, but for visibility in the Airflow UI and per-layer retries, not for gating. Locally and in CI, a single `dbt build` is correct.

---

## 12. Snowflake CI writes to a per-run schema, dropped with `if: always()`

**Rejected:** a shared CI schema.

**Why:** two concurrent runs writing to the same schema produce race conditions that look like flaky tests, and flaky tests get re-run rather than investigated.

The `if: always()` on the cleanup step matters as much as the isolation: a cleanup that only runs on success leaves a schema behind for every failed run. The `drop_ci_schemas` macro refuses to run unless the target is `ci` and the schema is `ci_<run id>`, and drops only schemas with that exact prefix, so it can never touch raw data, a developer schema or another run.

---

## 13. dbt's default schema naming, no override

**Revised.** The original described a `generate_schema_name` override so production tables land in `marts` rather than `analytics_marts`. The macro didn't exist, and neither did a production target. dbt's default — `<target schema>_<custom schema>` — is exactly what isolates developers (`dbt_jane_marts`) and CI runs (`ci_123456_marts`). The override is worth adding together with a production target, not before.

---

## 14. Grants come from Snowflake future grants, not a dbt hook

**Revised.** The original used an `on-run-end` hook calling a `grant_select_on_schemas` macro (which didn't exist — every `dbt run` would have failed at the end), reasoning that "future grants cover new objects but not objects recreated in place."

That's not how Snowflake behaves. dbt rebuilds tables and views with `CREATE OR REPLACE`, which creates a *new* object, and future grants are applied to new objects. (Snowflake's pending 2026_08 behavior change copies existing grants on `CREATE OR REPLACE` by default instead; either way the analyst role keeps access.) `snowflake/setup/01_account_setup.sql` grants `SEC_ANALYST` usage on future schemas and select on future tables and views in the database, once, and nothing in dbt has to remember to do it.

---

## 15. DuckDB as a local stand-in for Snowflake

**Rejected:** requiring a Snowflake account to run anything; mocking the warehouse.

**Why:** a reviewer, or a CI runner, should be able to execute every model and every test without credentials. DuckDB runs the same dbt project from a single file, with a few portability rules: three warehouse-specific expressions go through dispatched macros (`parse_yyyymmdd`, and dbt's `datediff`/`dateadd`), and the incremental strategy is `merge` on Snowflake, `delete+insert` on DuckDB — both upsert on `fact_key`.

**Trade-off:** two dialects to keep compatible, and DuckDB passing is not Snowflake passing. `scripts/check_snowflake_sql.py` closes most of that gap offline by compiling every model with the real Snowflake adapter and parsing the result with two Snowflake grammars; the integration workflow closes the rest.

---

## 16. Company history is derived from filings, not snapshotted

**Rejected:** a dbt snapshot of current company attributes.

**Why:** every filing already records the company's attributes as of that filing, so the full history is in the source. A snapshot only records changes from the day it starts running, dates them by when *we* noticed, and loses everything if the snapshot table is ever rebuilt. The derived model (gaps-and-islands over filings) has business-dated versions, is reproducible from scratch, and corrects itself when older quarters are backfilled. See MODELING.md > Company history.

**Trade-off:** surrogate keys of versions can change after a backfill, so only fully rebuilt tables reference `company_history_key`.

---

## 17. Raw data lands as text, mapped by header, in one transaction per quarter

**Rejected:** typed raw tables; `COPY` by position.

**Why:**
* **All `VARCHAR`:** a malformed value can never make the load drop or reject a row. Typing happens in staging with `try_` casts, where a test reports what failed to parse.
* **Mapped by header name:** the SEC added a column to `num.txt` in December 2024. Positional loading would have shifted every later column silently. The loader maps columns by name, accepts known optional columns that older files lack, and rejects unknown columns with a message saying what to update.
* **Field counts checked in Python:** Snowflake ignores `ERROR_ON_COLUMN_COUNT_MISMATCH` when `COPY` uses a transformation (which the column mapping needs), so the loader counts fields itself.
* **One transaction per quarter, then reconcile:** delete the quarter, `COPY` it with `FORCE = TRUE` (Snowflake otherwise silently skips files it loaded in the last 64 days), compare loaded rows with the file's row count, and roll back on any mismatch. Reloading a quarter replaces it; a failure leaves the previous load intact.

---

## 18. Key-pair authentication only; credentials only in environment variables

**Rejected:** password authentication; a `profiles.yml` copied into `~/.dbt` by hand.

**Why:** Snowflake is blocking password-only sign-in, starting with service users (Aug–Oct 2026 rollout), and key pairs are its recommended method for programmatic access. The committed `profiles/profiles.yml` reads every credential through `env_var()` — dbt 1.12 also loads a local `.env` automatically — and `scripts/check_project.py` fails CI if a literal value ever appears in a credential field.

---

## 19. Pinned toolchain: dbt Core 1.12, dbt_utils from a git tag

**Why dbt 1.12:** the original pinned dbt 1.8, which dbt Labs now lists as deprecated. 1.12 is the actively supported 1.x line (through July 2027). dbt v2 (a Rust rewrite) became the default for new installs in September 2026; this project parses with zero deprecation warnings (v2's main migration requirement) and passes dbt 1.12's `dbt parse --use-v2-parser` check, but it has not been built or tested on v2, so the requirement stays `<2.0.0`.

**Why git for dbt_utils:** the same release (1.4.1), pinned by tag and locked by `package-lock.yml`, installs from github.com alone. Unused packages from the original list (`dbt_expectations`, `codegen`, `audit_helper`) were removed.

---

## 20. Synthetic sample data with a known answer

**Rejected:** a slice of real SEC data as the test fixture.

**Why:** the SEC data is public domain and the loader handles it, but a test fixture needs a *known answer*. The sample is ten fictional companies, each built to exercise one behaviour (an amendment, a rename, a late filer, a bank, segment rows...), generated deterministically by `ingestion/sample_data.py`. The same specification drives `python_tests/test_expected_outputs.py`, which re-derives the expected marts in plain Python — two independent implementations that must agree.

**Trade-off:** synthetic data only exercises the cases someone thought of. Real-data runs (`make load-sec`) remain the way to find the rest.
