# Design Decisions

Each entry records the choice, what was rejected, and the trade-off accepted. Written for the version of me who returns in six months and wonders why something is the way it is.

---

## 1. Intermediate models materialize as tables

**Rejected:** ephemeral.

**Why:** ephemeral models are inlined as CTEs into every consumer. `int_facts__classified` has four downstream consumers, so ephemeral means Snowflake derives the same result four times per build. `int_filings__deduplicated` has three.

**Trade-off:** more storage, and intermediate tables show up in the warehouse where a curious analyst might query them directly. Mitigated by keeping them in a separate `intermediate` schema that isn't granted to `ANALYST`.

---

## 2. Only the atomic fact table is incremental

**Rejected:** incremental everywhere.

**Why:** `fct_financial_facts` is the only model where a full refresh is genuinely expensive — tens of millions of rows across 15 years. Everything else rebuilds in seconds to low minutes.

Incremental models carry real cost: state to reason about, a `unique_key` that must actually be unique, an `is_incremental()` branch that behaves differently from the full-refresh path, and a whole class of "why is this row stale" bugs. Paying that to save 40 seconds on a dimension table is a bad trade.

**The incremental filter uses `filed_date`, not `period_end_date`.** A filing submitted today reports periods going back years — filtering on period end would skip most newly-arrived data. A 7-day overlap window absorbs late corrections, and the merge on `fact_key` makes reprocessing the overlap a no-op.

---

## 3. `safe_divide()` everywhere

**Rejected:** raw `/`, and `nullif()` written inline at each site.

**Why:** Snowflake raises on `x / 0` and returns NULL on `x / NULL`. Neither is right for an analytical model. A ratio with a zero denominator is *undefined*, and NULL is the honest representation.

Writing `nullif()` inline at each of ~25 division sites means 25 chances to forget. A macro means one place to get it right, and one place to change the behaviour if the team later decides zero should yield zero.

**Trade-off:** slightly noisier SQL, and `dbt compile` output is harder to eyeball.

---

## 4. Ratios are NULLed where the sign makes them meaningless

**Rejected:** computing them anyway and letting the analyst filter.

**Why:** a company with negative equity and negative net income produces a *positive* ROE. That number is not just imprecise — it's actively misleading, and it will end up on a dashboard sorted descending, putting the most distressed companies at the top of a "best performers" list.

`case when total_equity > 0 then ... end` costs one line and prevents that.

**Trade-off:** more NULLs to explain. Documented per-column in MODELING.md.

---

## 5. Sector mapping is a 65-row range seed

**Rejected:** an 8,500-row seed with one line per SIC code. **Also rejected:** a `CASE` statement in the model.

**Why:** SIC is inherently range-based (2830–2836 is pharma), so ranges are the honest representation and 130x smaller in git. A `CASE` statement would bury business logic in SQL where a non-engineer can't update it.

**The catch:** a `BETWEEN` join fans out if ranges overlap. One overlapping range silently duplicates every company in the overlap, and duplicate rows in a company dimension fan out every downstream fact join — producing inflated totals with no error anywhere.

`assert_sic_ranges_do_not_overlap.sql` is what makes the range join safe. The seed and the test are a package; neither is correct without the other.

---

## 6. Amendments are kept, not discarded at ingestion

**Rejected:** filtering `form LIKE '%/A'` at the staging layer.

**Why:** discarding them at ingestion is simpler and makes every downstream model cleaner. It also makes restatement analysis permanently impossible, and a material restatement is one of the more interesting signals in financial data.

The cost of keeping them is contained: exactly one model (`int_filings__deduplicated`) handles resolution, and exactly one model (`mart_restatements`) deliberately bypasses it.

---

## 7. `assert_balance_sheet_balances` warns instead of erroring

**Rejected:** `severity: error`.

**Why:** a small number of companies legitimately fail A = L + E as modeled here — minority interest, redeemable preferred classified outside permanent equity, and filers who simply don't tag a subtotal. Blocking every deploy on those teaches the team to bypass the test, which is worse than not having it.

The test escalates to an error past 2,000 failures (`error_if: '>2000'`). At that volume the mapping is broken, not the filers.

**This is the general principle for test severity here:** a test that fires constantly gets ignored, and an ignored test is worse than no test because it creates false confidence.

---

## 8. Medians, not means, in sector benchmarks

**Rejected:** `avg()`.

**Why:** financial ratios are heavily right-skewed. One company with a near-zero equity base and a 400x return ratio drags a sector mean somewhere useless. An analyst asking "is a 22% operating margin good for software?" wants the median and the interquartile range.

Sectors with fewer than 5 companies in a year are excluded entirely. A "median" over three companies isn't a benchmark, and publishing it invites misuse.

---

## 9. Slim CI with deferral

**Rejected:** a full `dbt build` on every pull request.

**Why:** a full build costs minutes and warehouse credits per PR, most of which rebuild models the PR didn't touch. `--select state:modified+ --defer --state ./prod-artifacts` builds only what changed plus its downstream dependents, resolving unchanged `ref()`s against production.

**Trade-off:** it depends on a production `manifest.json` being published on every merge. The workflow falls back to a full build when the manifest is missing, so a first run or a lost artifact degrades rather than breaks.

---

## 10. The Airflow DAG runs monthly for a quarterly source

**Rejected:** a quarterly schedule aligned to SEC publication.

**Why:** the SEC publishes roughly six weeks after quarter close but doesn't commit to a date. A quarterly schedule that misses by a week leaves the warehouse stale for a full month. A monthly poll that finds nothing costs one HEAD request.

**Missing data is a skip, not a failure.** For most monthly runs the target quarter isn't published yet — that's the expected state. `AirflowSkipException` keeps the DAG green so that a red DAG still means something.

---

## 11. Staging is tested before intermediate runs

**Rejected:** `dbt build` in one shot, letting dbt interleave.

**Why:** `dbt build` runs tests as soon as each model's dependencies are met, which means marts can start building while staging tests are still running. If a source test fails, the marts have already burned credits producing a plausible-looking wrong answer.

Splitting the DAG into `run staging → test staging → run intermediate → ...` costs a few task transitions and guarantees nothing downstream is built on data that already failed validation.

---

## 12. CI writes to a per-PR schema, dropped with `if: always()`

**Rejected:** a shared CI schema.

**Why:** two concurrent pull requests writing to the same schema produce race conditions that look like flaky tests, and flaky tests get re-run rather than investigated.

The `if: always()` on the cleanup step matters as much as the isolation. A cleanup that only runs on success leaves a schema behind for every failed PR — and failed PRs are exactly the ones that come in bursts.

---

## 13. `generate_schema_name` is overridden

**Rejected:** dbt's default behaviour.

**Why:** dbt's default concatenates target schema and custom schema in *all* environments, so production tables land in `analytics_marts` rather than `marts`. The override gives clean names in production and prefixed names everywhere else:

```
prod : marts
dev  : dbt_karthik_marts
ci   : pr_412_marts
```

This is what makes per-developer and per-PR isolation work without touching any model config.

---

## 14. Grants are re-applied via `on-run-end`

**Rejected:** granting once during account setup.

**Why:** dbt drops and recreates tables, which discards their grants. Without the hook, a BI tool that worked yesterday returns "object does not exist" today — and it presents as a data problem rather than a permissions one, which costs an afternoon to diagnose.

`FUTURE GRANTS` cover new objects but not objects recreated in place, so both mechanisms are used.
