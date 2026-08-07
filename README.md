# SEC Financial Analytics — dbt on Snowflake

Modeling the SEC's public Financial Statement Data Sets into a tested, documented analytics layer: 24 dbt models across staging, intermediate and mart layers, 155 tests, orchestrated by Airflow, with CI that builds and tests every pull request against a real warehouse.

![dbt](https://img.shields.io/badge/dbt-1.8-orange)
![Snowflake](https://img.shields.io/badge/snowflake-cloud-blue)
![Airflow](https://img.shields.io/badge/airflow-2.8-teal)
![Tests](https://img.shields.io/badge/tests-155-green)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

---

## The dataset, and why it's harder than it looks

The [SEC Financial Statement Data Sets](https://www.sec.gov/dera/data/financial-statement-data-sets.html) contain every numeric fact from every XBRL filing since 2009 — millions of rows per quarter across four pipe-delimited files. It's free, real, and messy in ways that make it a genuine modeling exercise rather than a `SELECT *` demo.

Four problems have to be solved before a single number is trustworthy:

**1. The fact grain is seven columns wide.** A single (filing, tag) pair legitimately appears many times — once per period end date, per duration, per co-registrant, per unit. Grouping on (filing, tag) alone looks correct and silently over-counts. The surrogate key in `stg_sec__numeric_facts` encodes the true grain so it can be *tested* rather than assumed.

**2. Annual and quarterly figures live in the same column.** `qtrs = 0` is a point-in-time balance, `qtrs = 1` is one quarter, `qtrs = 4` is a full year. Summing across them inflates revenue roughly fivefold. Every downstream model carries duration in its grain, and `assert_no_annual_quarterly_mixing.sql` fails the build if one leaks into the other.

**3. There is no single revenue tag.** Depending on filer and taxonomy year, top-line revenue is `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`, or several others. A 68-row seed maps every known variant to a canonical concept with a priority, so the most specific tag wins when a filer reports more than one.

**4. Filings get amended.** A 10-K followed by a 10-K/A means two rows for the same fiscal year. `int_filings__deduplicated` resolves to one authoritative filing — but amendments are kept in the warehouse, because `mart_restatements` compares them against their originals and a restatement that moves net income 40% is the interesting event, not noise to discard.

---

## Architecture

```
        SEC EDGAR (quarterly zip: sub, num, tag, pre)
                          │
                 scripts/load_sec_data.py
                  PUT → stage → COPY INTO
                          │
              ┌───────────▼────────────┐
              │  RAW_SEC  (Snowflake)  │  no casting, no cleaning
              │  SUB  NUM  TAG  PRE    │  _loaded_at, _source_file
              └───────────┬────────────┘
                          │
   ┌──────────────────────▼───────────────────────┐
   │  STAGING  (views, 4 models)                  │
   │  rename · cast YYYYMMDD → date · 1/0 → bool  │
   │  no joins, no dedup, no business logic       │
   └──────────────────────┬───────────────────────┘
                          │
   ┌──────────────────────▼───────────────────────┐
   │  INTERMEDIATE  (tables, 8 models)            │
   │  · dedupe amended filings                    │
   │  · classify facts to statements (via pre)    │
   │  · pivot tags → named concepts (IS/BS/CF)    │
   │  · join IS + BS + CF at matching grain       │
   │  · derive Q4 = annual − Q1 − Q2 − Q3         │
   └──────────────────────┬───────────────────────┘
                          │
   ┌──────────────────────▼───────────────────────┐
   │  MARTS  (tables, 12 models)                  │
   │  core    : dim_company, dim_date,            │
   │            dim_filing, dim_financial_tag,    │
   │            fct_financial_facts (incremental) │
   │  finance : annual & quarterly financials,    │
   │            ratios, YoY growth, sector        │
   │            benchmarks, restatements, DQ      │
   └──────────────────────┬───────────────────────┘
                          │
                    BI / analysis
```

Layer contracts and the reasoning behind each decision: **[docs/MODELING.md](docs/MODELING.md)**

---

## Models

| Layer | Model | What it does |
|---|---|---|
| staging | `stg_sec__submissions` | One row per filing, typed and renamed |
| staging | `stg_sec__numeric_facts` | One row per numeric fact, seven-column grain |
| staging | `stg_sec__tags` | XBRL tag dictionary per taxonomy version |
| staging | `stg_sec__presentation` | Statement classification per fact |
| intermediate | `int_filings__deduplicated` | One authoritative filing per company-period |
| intermediate | `int_facts__classified` | Facts + statement + tag metadata |
| intermediate | `int_facts__income_statement` | IS concepts pivoted to columns |
| intermediate | `int_facts__balance_sheet` | BS concepts pivoted to columns |
| intermediate | `int_facts__cash_flow` | CF concepts pivoted to columns |
| intermediate | `int_companies__enriched` | Company attributes + sector + activity |
| intermediate | `int_financials__annual` | IS + BS + CF joined per company-year |
| intermediate | `int_financials__quarterly` | Quarterly with Q4 derived |
| core | `dim_company` | Company dimension (Type 1) |
| core | `dim_date` | Generated calendar dimension |
| core | `dim_filing` | Filing dimension with days-to-file |
| core | `dim_financial_tag` | Tag dimension with usage stats |
| core | `fct_financial_facts` | Atomic fact table, incremental |
| finance | `fct_company_financials_annual` | Primary analytical table |
| finance | `fct_company_financials_quarterly` | Quarterly with QoQ / YoY |
| finance | `fct_financial_ratios` | Profitability, liquidity, leverage, efficiency |
| finance | `fct_yoy_growth` | YoY change and 3-year CAGR |
| finance | `mart_sector_benchmarks` | Sector medians and percentiles |
| finance | `mart_restatements` | Amended vs original filings |
| finance | `mart_data_quality_summary` | Coverage by fiscal year |

---

## Testing

155 tests: 147 schema tests plus 8 singular tests. The schema tests are mostly table stakes — uniqueness, not-null, referential integrity, accepted values. The singular tests are where the domain knowledge lives:

| Test | What it catches |
|---|---|
| `assert_balance_sheet_balances` | A = L + E within tolerance. Breaks when tag mapping is wrong. |
| `assert_fact_grain_is_unique` | The seven-column grain. Dropping any one column looks harmless and isn't. |
| `assert_no_annual_quarterly_mixing` | Annual revenue smaller than a single quarter of the same year. |
| `assert_one_authoritative_filing_per_period` | Proves the dedup logic actually worked. |
| `assert_period_end_precedes_filing` | A filing submitted before its period ended — date parsing bug. |
| `assert_ratios_within_plausible_bounds` | Gross margin > 100%, current ratio > 1000. |
| `assert_no_orphan_facts` | Facts pointing at dimensions that no longer contain them. |
| `assert_sic_ranges_do_not_overlap` | Guards the range join in the sector seed against fan-out. |

Two custom generic tests:

- **`not_null_proportion`** — plain `not_null` is too strict for a public dataset where some filers genuinely omit R&D or inventory. But a column going from 95% populated to 12% is a real incident. This catches the regression without failing on legitimate sparsity.
- **`expression_between`** — bounds on a SQL expression rather than a bare column, so derived ratios can be tested without materializing them first.

**Severity is deliberate.** `assert_balance_sheet_balances` warns rather than errors: a handful of companies with minority interest or redeemable preferred will legitimately fail, and blocking deploys on those trains the team to ignore the test. It escalates to an error past 2,000 failures — at that point the mapping is broken, not the filers.

---

## Continuous integration

Every pull request runs against a real Snowflake warehouse in an isolated schema:

1. **Lint** — `sqlfluff` on all models, plus a custom check that every model has a description and at least one test. An undocumented model is one nobody can safely reuse.
2. **Slim CI build** — downloads the production `manifest.json` and runs `dbt build --select state:modified+ --defer --state`. Only changed models and their downstream dependents get built. On this project that's the difference between a 12-minute check and a 90-second one.
3. **Isolated schema** — each PR writes to `pr_<number>`, dropped afterwards with `if: always()`. Without that cleanup, every merged PR leaves an orphaned schema behind forever.

On merge to `main`, the deploy workflow builds production, runs snapshots, publishes the new `manifest.json` to S3 (which becomes tomorrow's Slim CI baseline), and pushes `dbt docs` to GitHub Pages.

---

## Orchestration

`airflow/dags/sec_financials_pipeline.py` runs **monthly, not quarterly**. The SEC publishes roughly six weeks after each quarter closes but doesn't commit to a date. A monthly poll that finds nothing new is much cheaper than a quarterly schedule that misses by a week and leaves the warehouse stale for a month.

The DAG resolves its own target quarter in code rather than hardcoding it, HEADs the zip before spending a task slot downloading, and raises `AirflowSkipException` — not a failure — when the quarter isn't published yet. A missing quarter is the *expected* state for most monthly runs; treating it as a failure means a red DAG most months and an alert channel everyone learns to ignore.

Staging is tested before intermediate runs. Building marts on data that already failed its source tests burns warehouse credits to produce a plausible-looking wrong answer, which is worse than no answer.

---

## Quick start

**Prerequisites:** Python 3.10+, a Snowflake account, and a contact email for the SEC's required User-Agent.

```bash
git clone https://github.com/<your-username>/sec-analytics-dbt.git
cd sec-analytics-dbt

make install          # dbt-core, dbt-snowflake, sqlfluff
make deps             # dbt packages
```

Set up Snowflake — run these in a worksheet, in order:

```
snowflake/setup/01_account_setup.sql   (as ACCOUNTADMIN)
snowflake/setup/02_raw_tables.sql      (as LOADER)
```

Configure credentials:

```bash
cp profiles.yml.template ~/.dbt/profiles.yml
export SNOWFLAKE_ACCOUNT=your_account
export SNOWFLAKE_USER=your_user
export SNOWFLAKE_PRIVATE_KEY_PATH=~/.ssh/snowflake_key.p8
export SEC_USER_AGENT="Your Name your.email@example.com"   # required by SEC policy
```

Load data and build:

```bash
make load-data YEARS=2022:2024    # ~3 GB, takes a while
make build                        # seed + run + test + snapshot
make docs                         # lineage graph at localhost:8080
```

`dev` and `ci` targets are automatically limited to the last 3 years via the `limit_data_in_dev` macro, so a laptop build takes seconds rather than minutes. Production gets full history.

---

## Repository layout

```
├── models/
│   ├── staging/sec/         4 models + sources.yml + models.yml
│   ├── intermediate/        8 models + models.yml
│   └── marts/
│       ├── core/            5 dimensional models
│       └── finance/         7 analytical models
├── tests/
│   ├── generic/             2 reusable custom tests
│   └── *.sql                8 singular tests
├── macros/                  safe_divide, date casting, dev limiting,
│                            schema naming, grants, PR cleanup
├── seeds/
│   ├── financial_tag_mapping.csv   68 tag → concept mappings
│   └── sic_sector_mapping.csv      65 SIC ranges → sectors
├── snapshots/               Type 2 SCD on company attributes
├── analyses/                Compiled but never materialized
├── airflow/dags/            Monthly ingest + transform DAG
├── snowflake/setup/         Warehouses, roles, grants, raw DDL
├── scripts/                 SEC loader + documentation checker
├── .github/workflows/       CI (Slim) and production deploy
└── docs/
    ├── MODELING.md          Layer contracts and design decisions
    └── DECISIONS.md         Trade-offs, with what was rejected and why
```

---

## Design decisions worth defending

A few choices here are non-obvious enough that an interviewer will ask. Full reasoning in [docs/DECISIONS.md](docs/DECISIONS.md); the short version:

**Intermediate models are tables, not ephemeral.** Several are referenced 3+ times downstream. Ephemeral would inline the same CTE into each consumer and make Snowflake recompute the identical scan every time.

**Only `fct_financial_facts` is incremental.** It's the only model where a full refresh is genuinely expensive. Making everything incremental adds state, edge cases, and a whole class of "why is this row stale" bugs in exchange for saving seconds.

**Every division goes through `safe_divide()`.** A company with zero equity isn't "infinitely leveraged" — its leverage ratio is *undefined*. NULL says that honestly; a huge number lies.

**Ratios are NULLed where inputs make them meaningless.** Negative equity makes ROE uninterpretable, not just negative. Guarding the denominator sign prevents a distressed company from looking highly profitable.

**Sector mapping is a 65-row range seed, not 8,500 rows.** SIC is inherently range-based, so a range seed is smaller in git and the honest representation. The range join is only safe because `assert_sic_ranges_do_not_overlap` guarantees no overlaps — without that test, a one-character typo silently duplicates every company in the overlap and fans out every downstream join.

**Medians, not means, in sector benchmarks.** Financial ratios are heavily right-skewed. One company with a near-zero equity base drags a mean somewhere useless.

---

## Notes before you rely on this

The `dev_lookback_years` var means dev and CI see a 3-year slice. If you're comparing your output to someone else's, check which target you built against.

Q4 in the quarterly mart is **derived**, not reported — there is no Q4 10-Q. Filter on `is_derived_q4` and `has_complete_quarters` before drawing conclusions from a quarterly trend.

`mart_data_quality_summary.is_analysis_ready` exists because "the pipeline ran successfully" and "the data is usable" are different claims. Check it before cross-company analysis on a given year.

---

## What I'd add next

- **dbt-elementary** for test-result history and anomaly detection over time; a test that passes today but has been degrading for six weeks is invisible right now
- **Snapshot the tag mapping seed** so a retroactive mapping change is auditable
- **Unit tests** (dbt 1.8 `unit_tests:`) with fixture rows for the pivot and Q4-derivation logic, which are the two places a subtle bug would be hardest to spot
- **Incremental `fct_company_financials_annual`** once the history outgrows a full rebuild
- **Cross-reference against XBRL company facts API** to validate a sample of computed figures against the SEC's own rendering

---

## License

MIT — see [LICENSE](LICENSE).
