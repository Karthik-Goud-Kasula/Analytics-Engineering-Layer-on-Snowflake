# Modeling Guide

## Layer contracts

Each layer guarantees something the next layer is allowed to assume. Writing these down is what stops "just one more transformation here" from turning the project into an unreviewable pile of CTEs.

| Layer | Materialization | Guarantees | Explicitly does NOT guarantee |
|---|---|---|---|
| **Staging** | view | 1:1 with source rows. Renamed, typed, trimmed. | Deduplicated. Joined. Business-meaningful. |
| **Intermediate** | table | Deduplicated. Statement-classified. Concepts resolved from tags. Grain declared and tested. | Denormalized. Presentation-ready. |
| **Marts** | table | One row per documented grain. Descriptors joined in. Safe for BI. | Complete — coverage varies by year, see `mart_data_quality_summary`. |

**Staging is a view, always.** It's a rename layer; materializing it doubles storage for zero benefit, and a view means schema changes propagate without a rebuild.

**Intermediate is a table, not ephemeral.** This is the choice people most often get wrong. Ephemeral models get inlined as CTEs into every consumer. `int_facts__classified` is referenced by four downstream models — ephemeral would make Snowflake scan and re-derive the same result four times per build. Materializing once costs storage and saves compute, and compute is what the bill is made of.

---

## The seven-column fact grain

This is the thing to understand about the SEC datasets.

`num.txt` looks like it's keyed on (accession number, tag). It isn't. The real grain is:

```
adsh     accession number   -- which filing
tag      XBRL element       -- which concept
version  taxonomy version   -- which definition of that concept
ddate    period end         -- filings carry prior-year comparatives
qtrs     duration           -- annual AND quarterly figures coexist
uom      unit               -- USD vs shares vs pure
coreg    co-registrant      -- subsidiaries file alongside parents
```

Drop any one and the result is wrong in a way that looks fine:

- Drop `ddate` → prior-year comparatives merge into the current year
- Drop `qtrs` → annual and quarterly collapse together, revenue inflates ~5x
- Drop `coreg` → subsidiary figures merge into the parent's
- Drop `version` → a redefined tag from two taxonomies conflicts

`stg_sec__numeric_facts` builds a surrogate key over all seven, and `assert_fact_grain_is_unique.sql` fails the build if it isn't unique. That test is the single most valuable one in the project.

---

## Statement classification

A tag name doesn't tell you which statement it belongs to. `Revenues` appears on the income statement, in segment footnotes, and in the MD&A section. Classification comes from `pre.txt`, the presentation linkbase, which records where each tag was actually rendered in that specific filing.

Two filters applied in `int_facts__classified`:

**Parenthetical facts are dropped** (`inpth = 1`). These are the "(net of $12,000 allowance)" figures shown inline on the face of a statement. They're not line items, and including them puts non-line-item numbers into line-item aggregates.

**Abstract tags are dropped.** They're grouping headers with no value of their own.

When a tag appears on more than one statement in the same filing — cash shows on both the balance sheet and the cash flow statement — a `row_number()` with an explicit statement priority picks one primary classification, so each fact is counted once.

---

## Tag → concept mapping

`seeds/financial_tag_mapping.csv` maps 68 XBRL tags to 40 canonical concepts, with a priority column.

Priority matters when a filer reports two tags that both map to the same concept. Example: a company tags both `RevenueFromContractWithCustomerExcludingAssessedTax` (priority 1) and `Revenues` (priority 3). The more specific, more recent ASC 606 tag wins. Without a priority, `max()` picks arbitrarily and the answer changes between runs.

**Custom tags are excluded from the pivots, not from the warehouse.** A filer's own revenue tag is the right number *for that filer* — it's just not comparable across companies, and the marts exist for cross-company analysis. They stay available in `fct_financial_facts` and are flagged in `dim_financial_tag`.

Adding a tag is a one-line seed change plus a `dbt seed`. That's the point of putting the mapping in a seed rather than a `CASE` statement buried in SQL.

---

## Duration handling

`qtrs` is the only reliable signal for period type:

| `qtrs` | Meaning | Applies to |
|---|---|---|
| 0 | Instant (point in time) | Balance sheet |
| 1 | One quarter | Income statement, cash flow |
| 4 | Full year | Income statement, cash flow |

`int_financials__annual` joins income statement (`qtrs = 4`) to balance sheet (`qtrs = 0`) on `period_end_date`. **The duration filter on each side is what prevents fan-out.** Joining on `period_end_date` alone multiplies every company-year by the number of durations present.

---

## Q4 derivation

There is no Q4 10-Q. A company files three 10-Qs and one 10-K, and the 10-K covers the full year. So Q4 has to be computed:

```
Q4 = annual − Q1 − Q2 − Q3
```

This only works for **flow** measures (revenue, net income, cash flow). Balance sheet items are instants — the year-end balance *is* the Q4 balance, no arithmetic needed.

Two safeguards in `int_financials__quarterly`:

- The derivation only runs when all three prior quarters are genuinely present (`quarters_reported = 3`). Deriving from two quarters would produce a confidently wrong number.
- `is_derived_q4` is exposed on every row, so downstream consumers can tell reported from computed.

The subtraction inherits any restatement that happened between the quarterly filings and the 10-K. That's a real limitation, not a bug — it's why `assert_no_annual_quarterly_mixing` excludes derived Q4 rows from its check.

---

## Amendment handling

`int_filings__deduplicated` resolves to one authoritative filing per (company, fiscal year, fiscal period, base form type):

1. Drop submissions flagged `prevrpt` — the SEC has already told us they were superseded.
2. Among what remains, keep the most recently **filed** one. Filed date, not period date, because an amendment shares its original's period.
3. Deterministic tiebreak on `filing_id` so the same input always produces the same output.

Amendments aren't deleted from the warehouse. `mart_restatements` reads from `stg_sec__submissions` directly — deliberately bypassing the dedup model, since that model's entire job is to hide amendments — and compares each amendment against its original. A restatement that moves net income more than 5% is flagged material, matching common SEC materiality guidance.

---

## Naming conventions

| Pattern | Meaning |
|---|---|
| `stg_<source>__<entity>` | Staging. Double underscore separates source from entity. |
| `int_<entity>__<verb>` | Intermediate. The verb says what was done. |
| `dim_<entity>` | Dimension. One row per entity, descriptive attributes. |
| `fct_<entity>` | Fact. Measures at a declared grain. |
| `mart_<subject>` | Aggregate or subject-specific analytical table. |
| `<entity>_key` | Surrogate key (hashed). Join target. |
| `<entity>_id` | Natural key from the source. |
| `is_` / `has_` | Boolean. Always. |
| `_date` / `_at` | Date vs timestamp. |

Booleans are named so the true case is obvious from the name alone: `is_derived_q4`, `has_complete_quarters`, `balance_sheet_balances`. A column called `q4_status` would require reading the model to interpret.

---

## Where nulls are meaningful

This dataset is full of legitimately-missing values, and conflating "not reported" with "zero" is the most common way to produce a wrong answer that looks right.

| Column | Null means |
|---|---|
| `revenue` | The filer didn't tag revenue with a mapped concept — **not** zero revenue |
| `research_and_development` | Company doesn't report R&D separately — common outside tech |
| `inventory` | Service businesses genuinely have none |
| `gross_margin` | Revenue was zero or null, so the ratio is undefined |
| `return_on_equity` | Equity was zero or negative, making the ratio uninterpretable |
| `revenue_growth_pct` | Prior-year base was zero or negative |

Companion boolean flags — `has_balance_sheet`, `has_cash_flow`, `has_revenue` — let a consumer distinguish "we looked and it wasn't there" from "we didn't look". `mart_data_quality_summary` aggregates these by year so the question "is this year usable?" has an answer.
