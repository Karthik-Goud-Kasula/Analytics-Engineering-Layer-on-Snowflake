# Modeling Guide

## Layer contracts

Each layer guarantees something the next layer is allowed to assume. Writing these down is what stops "just one more transformation here" from turning the project into an unreviewable pile of CTEs.

| Layer | Materialization | Guarantees | Explicitly does NOT guarantee |
|---|---|---|---|
| **Raw** | table (loader) | Exactly what the SEC published, all `VARCHAR`, plus load metadata. Re-loading a quarter replaces it. | Typed. Valid. |
| **Staging** | view | One row per source row. Renamed, typed with `try_` casts. | Deduplicated (one exception, below). Joined. Business-meaningful. |
| **Intermediate** | table | Deduplicated. Statement-classified. Concepts resolved from tags. Grain declared and tested. | Denormalized. Presentation-ready. |
| **Marts** | table (one incremental) | One row per documented grain. Surrogate keys. Descriptors joined in. Safe for BI. | Complete — coverage varies by year, see `mart_data_quality_summary`. |

**Staging is a view, always.** It's a rename layer; materializing it doubles storage for zero benefit, and a view means schema changes propagate without a rebuild.

**The one staging exception: tags.** The SEC republishes every standard tag it uses in *each* quarter's `tag.txt`, so the raw table holds `(Assets, us-gaap/2023)` once per loaded quarter. That is a property of the file format, not of the data, so `stg_sec__tags` collapses it and every consumer can rely on a unique key.

**Intermediate is a table, not ephemeral.** Ephemeral models get inlined as CTEs into every consumer. `int_facts__classified` feeds three models and `int_filings__deduplicated` four — ephemeral would make the warehouse re-derive the same result in each. Materializing once costs storage and saves compute, and compute is what the bill is made of.

---

## The eight-column fact grain

This is the thing to understand about the SEC datasets.

`num.txt` looks like it's keyed on (accession number, tag). It isn't. The real grain, per the [SEC's documentation](https://www.sec.gov/files/financial-statement-data-sets.pdf), is:

```
adsh      accession number   -- which filing
tag       XBRL element       -- which concept
version   taxonomy version   -- which definition of that concept
ddate     period end         -- filings carry prior-year comparatives
qtrs      duration           -- annual AND quarterly figures coexist
uom       unit               -- USD vs USD/shares vs pure
segments  axis/member        -- segment breakdowns (added December 2024)
coreg     co-registrant      -- subsidiaries file alongside parents
```

Drop any one and the result is wrong in a way that looks fine:

- Drop `ddate` → prior-year comparatives merge into the current year
- Drop `qtrs` → annual and quarterly collapse together, revenue inflates ~5x
- Drop `segments` → revenue by product line is summed into total revenue
- Drop `coreg` → subsidiary figures merge into the parent's
- Drop `version` → a redefined tag from two taxonomies conflicts

`segments` arrived when the SEC reprocessed the data sets in December 2024; earlier documentation (and this project's first draft) described a seven-column grain. The loader still accepts older files without the column and loads it as NULL.

`stg_sec__numeric_facts` builds `fact_key` over all eight, and its `unique` test *is* the grain test.

The documented key is not quite unique in practice. In 2026q1, one 10-K reported
17 pairs of different forward-currency contracts, and the SEC published identical
typed-dimension text for each pair (`OpenForwardForeignCurrencyContractIdentifier=,
Wells Fargo B...`). Same eight columns, different values. Deduplicating would
silently drop real contracts, so staging keeps both rows. `grain_ordinal` numbers
rows that share all eight columns, and only the second and later rows get it in
`fact_key`, so every ordinary fact keeps its key. A warn-level test reports such
rows on each build. They are segment rows, so the consolidated financial marts
never see them.

---

## Statement classification

A tag name doesn't tell you which statement it belongs to. Classification comes from `pre.txt`, the presentation linkbase, which records where each tag was rendered in that specific filing.

Filters applied in `int_facts__classified`, and why:

| Filter | Why |
|---|---|
| Consolidated only: `segments` and `coreg` both NULL | Segment rows break a total down; co-registrant rows belong to a subsidiary. Either would double-count or replace the company's figure. |
| Current period only: fact date within 15 days of the filing's period | A 10-K also reports the prior year as a comparative — often recast. Taking each period from its own filing gives one consistent source per row. The 15-day slack absorbs the SEC's month-end rounding for 52/53-week fiscal years. |
| Not parenthetical (`inpth = 1`) | "(net of $12,000 allowance)" figures are not line items. |
| Not abstract | Grouping headers carry no value. |
| Value parsed | Unparseable values are reported by `assert_numeric_values_parse`, not silently used. |

When a tag appears on more than one statement in the same filing — net income on the income statement *and* the first line of the cash flow statement — a `row_number()` with an explicit statement priority (IS, BS, CF, CI, EQ) picks one, so each fact is counted once.

**"As reported" semantics.** Because each period comes from its own filing, the annual table shows what the company reported for that year in that year's 10-K (or its amendment). A later 10-K's recast comparative never silently replaces it. Restatements made by amending the filing are visible in `mart_restatements`; recasts inside later comparatives are not modelled.

---

## Tag → concept mapping

`seeds/financial_tag_mapping.csv` maps 62 US-GAAP tags to 33 canonical concepts (63 rows: one tag serves two concepts) with a `priority` and the expected `unit_of_measure`.

Priority matters when a filer reports two tags that both map to the same concept. Example: a company tags both `RevenueFromContractWithCustomerExcludingAssessedTax` (priority 1) and `Revenues` (priority 3). The more specific ASC 606 tag wins. Without a priority, `max()` picks arbitrarily and the answer changes between runs; a final tiebreak on tag name keeps it deterministic, and a seed test guarantees no two tags share a priority for the same concept.

The **unit** is part of the match. Earnings per share is reported in `USD/shares`; the original model filtered every fact to `USD`, which silently nulled EPS for every company.

The winning tag is kept (`revenue_tag`, `net_income_tag`), so any figure can be traced back to the exact fact it came from — and `assert_annual_figures_reconcile_to_atomic_facts` does exactly that.

**Custom tags are excluded from the pivots, not from the warehouse.** A filer's own revenue tag is the right number *for that filer* — it's just not comparable across companies, and the marts exist for cross-company analysis. Custom facts stay available in `fct_financial_facts`.

Adding a tag is a one-line seed change plus a `dbt seed`. That's the point of putting the mapping in a seed rather than a `CASE` statement buried in SQL.

---

## Duration handling

`qtrs` is the only reliable signal for period type:

| `qtrs` | Meaning | Applies to |
|---|---|---|
| 0 | Instant (point in time) | Balance sheet |
| 1 | One quarter | Income statement (10-Q) |
| 2, 3 | Year to date | Income statement and **all** cash flow in 10-Qs |
| 4 | Full year | Income statement, cash flow (10-K) |

`int_financials__annual` joins the income statement (`qtrs = 4`), balance sheet and cash flow (`qtrs = 4`) **on the same filing**. The duration filter on each side is what prevents fan-out: joining on company and period alone multiplies every company-year by the number of durations present.

10-Q cash flow statements are year-to-date only, so the quarterly mart carries income statement measures, not cash flow.

---

## Q4 derivation

There is no Q4 10-Q. A company files three 10-Qs and one 10-K, and the 10-K covers the full year. So Q4 has to be computed:

```
Q4 = annual − Q1 − Q2 − Q3
```

This only works for **flow** measures (revenue, net income). Balance sheet items are instants — the year-end balance *is* the Q4 balance.

Safeguards in `int_financials__quarterly`:

- Q4 is derived only when all three quarters were reported, and **per measure** only when all three quarters have that measure. Deriving from two quarters would produce a confidently wrong number.
- **EPS is never derived**: per-share figures are not additive (share counts change), so a derived Q4 has NULL EPS.
- `is_derived_q4` and `has_complete_quarters` are on every row.

The subtraction inherits any restatement made between the 10-Qs and the 10-K. That's a real limitation, not a bug — BIRCH in the sample data shows it — and it is why `assert_no_annual_quarterly_mixing` excludes derived Q4 rows. `assert_derived_q4_reconciles_to_annual` checks the four quarters sum back to the annual figure.

---

## Amendment handling

`int_filings__deduplicated` resolves to one authoritative filing per (company, period end date, base form type):

1. **Only filings that contain a primary financial statement compete.** A "Part III" 10-K/A, filed to add executive-compensation disclosures, has no statements at all. Letting it win because it is newer would erase the company's numbers for the year.
2. Among those, keep the most recently **filed** one. Filed date, not period date, because an amendment shares its original's period.
3. Deterministic tiebreaks (acceptance time, then accession number) so the same input always produces the same output.

The SEC's `prevrpt` flag ("subsequently amended") is deliberately **not** used to drop filings, even though it looks like the obvious filter: it is also set when the only amendment is a Part III amendment, and filtering on it would drop the one filing that has numbers. (The sample's CEDAR filings exercise exactly this.)

Amendments aren't deleted from the warehouse. `mart_restatements` reads every filing directly — deliberately bypassing the dedup model, since that model's whole job is to hide superseded filings — and compares each statement-bearing amendment with the original. A restatement that moves revenue or net income by more than 5% is flagged material: the common rule of thumb, which SEC Staff Accounting Bulletin 99 describes as a starting point for judgement, not a bright line.

---

## Company history (SCD Type 2)

`dim_company_history` tracks name, SIC code, state and country of incorporation, and fiscal year end over time.

It is a model, not a dbt snapshot, because the history is already in the source: every filing records the company's attributes *as of that filing*. Deriving versions from the data means effective dates are business dates (when the first filing showed the new attributes), the model is reproducible from scratch, and a backfill of older quarters corrects the history instead of being appended out of order — none of which a snapshot can do.

The versions come from gaps-and-islands: flag each filing whose attributes differ from the previous filing's, take a running sum of the flags as the version number. A company that changes A → B → A gets three versions; grouping by the attributes alone would wrongly merge the two A periods. A unit test covers exactly that case.

`valid_from` is inclusive, `valid_to` exclusive and NULL for the current version. `fct_company_financials_annual` uses a point-in-time join on the 10-K's acceptance time, so a company reclassified in 2025 keeps its 2023 numbers in its 2023 sector. `assert_company_history_has_no_overlaps` guarantees the windows chain without gaps or overlaps — an overlap would fan the join out, a gap would drop rows.

---

## The incremental fact table

`fct_financial_facts` holds every numeric fact at its native grain and is the one incremental model. Its watermark is **load time**, not filing date — see [DECISIONS.md #2](DECISIONS.md#2-only-the-atomic-fact-table-is-incremental) for why the original filed-date design would have dropped backfilled data, and `make incremental-check` for the proof that incremental and full-refresh builds match.

It stores only immutable attributes. Whether a filing is still authoritative, or what a company is called today, lives in the dimensions — an incremental row written before an amendment arrived can never carry a stale flag.

---

## Naming conventions

| Pattern | Meaning |
|---|---|
| `stg_<source>__<entity>` | Staging. Double underscore separates source from entity. |
| `int_<entity>__<verb>` | Intermediate. The verb says what was done. |
| `dim_<entity>` | Dimension. One row per entity (or entity version). |
| `fct_<entity>` | Fact. Measures at a declared grain. |
| `mart_<subject>` | Aggregate or subject-specific analytical table. |
| `<entity>_key` | Surrogate key (hash). Join target. |
| `<entity>_id` | Natural key from the source, in staging and intermediate. |
| `cik` | The company's natural key in marts — the identifier analysts know. |
| `is_` / `has_` | Boolean. Always. |
| `_date` / `_at` | Date vs timestamp. |
| `_rate` | A fraction: 0.17 means 17%. |

Booleans are named so the true case is obvious from the name alone: `is_derived_q4`, `has_complete_quarters`, `balance_sheet_balances`. A column called `q4_status` would require reading the model to interpret.

---

## Where nulls are meaningful

This dataset is full of legitimately missing values, and conflating "not reported" with "zero" is the most common way to produce a wrong answer that looks right.

| Column | Null means |
|---|---|
| `revenue` | The filer didn't tag revenue with a mapped concept — **not** zero revenue |
| `research_and_development` | Company doesn't report R&D separately — common outside tech |
| `inventory` | Service businesses genuinely have none |
| `total_debt` | No debt tag reported (may be no debt, or an unmapped debt tag) |
| `gross_margin` | Revenue was zero or null, so the ratio is undefined |
| `return_on_equity` | Equity was zero or negative, making the ratio uninterpretable |
| `cash_conversion_ratio` | Net income was zero or negative — same sign problem as ROE |
| `revenue_growth_rate` | Prior-year revenue was zero, negative, or the prior year isn't loaded |
| `eps_basic` on a derived Q4 | Per-share figures can't be derived by subtraction |

Companion flags — `has_revenue`, `has_balance_sheet`, `has_cash_flow`, `has_prior_year`, `has_complete_quarters` — let a consumer distinguish "we looked and it wasn't there" from "we didn't look". `mart_data_quality_summary` aggregates them by year so the question "is this year usable?" has an answer.
