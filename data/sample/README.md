# Sample data — synthetic

**Every company, CIK and number in this folder is fictional.** It is generated
by [`ingestion/sample_data.py`](../../ingestion/sample_data.py) and contains no
real company, personal or confidential information. CIKs are in the `99000xx`
range, far above any CIK the SEC has assigned.

The files follow the real SEC Financial Statement Data Sets layout exactly
(tab-delimited, UTF-8, lowercase header, the current `num.txt` layout with
`segments`), so the same loader and dbt models run unchanged on real data.

Regenerate with `make sample-data`; CI fails if these files drift from the
generator (`make check-sample-data`).

## Quarters

Each folder is one SEC quarterly data set: the filings **submitted** in that
quarter (not the fiscal periods they report on).

| Folder | Contents |
|---|---|
| `2023q1` | FY2022 10-Ks (six companies); FIR's fiscal-Q2-2023 10-Q |
| `2023q2` – `2023q4` | 2023 10-Qs; FIR's FY2023 10-K (June year end) in `2023q3` |
| `2024q1` | FY2023 10-Ks; CEDAR's Part III 10-K/A |
| `2024q2` | Q1 2024 10-Qs, BIRCH's restating 10-K/A, ELM's late 10-K, JUNIPER's 20-F |

`make incremental-check` loads them in three batches — `2023q2`–`2024q1`, then
`2024q2`, then `2023q1` (a backfill of older data, loaded last) — and checks the
result is identical to loading everything at once.

## Scenarios

| Company | What it tests |
|---|---|
| ALDER SOFTWARE | Reports revenue under two tags; the ASC 606 tag must win. Prior-year comparatives (recast by 1%) must not replace as-reported values. Net income on three statements (IS, CF, CI) must be counted once. |
| BIRCH DATA SYSTEMS → BIRCH CLOUD | Renamed in April 2024 (SCD2: two versions). FY2023 10-K amended with a 10-K/A that restates SG&A, cutting net income ~22% (material restatement; the amendment becomes authoritative). |
| CEDAR NETWORKS | Co-registrant (subsidiary) and segment rows beside the consolidated revenue; neither may reach company totals. A Part III-only 10-K/A with no financial statements must not replace the original, even though the original is flagged `prevrpt`. |
| DOGWOOD LABS | Pre-revenue (revenue = 0, so margins are NULL, not errors). Negative equity in FY2023 (ROE NULL). Q2 10-Q missing, so no Q4 is derived. |
| ELM COMMERCE | Late filer: the FY2023 10-K arrives in `2024q2`. Revenue under `Revenues` plus a custom tag that must be ignored. One deliberately unparseable value (`12O5000`) that staging nulls and a warn-level test reports. |
| FIR MEDICAL DEVICES | June fiscal year end: fiscal quarters span calendar years. FY2024 has Q1–Q3 but no 10-K yet, so no derived Q4. |
| GROVE REGIONAL BANCORP | Bank: no gross profit, no current assets or liabilities, no tagged debt (ratios NULL). |
| HAZEL BIOTHERAPEUTICS | Redeemable preferred stock (temporary equity), so A ≠ L + E as modelled: the balance-sheet test warns, as designed. |
| IRONWOOD SECURITY SOFTWARE | Pre-ASC 606 revenue tag (`SalesRevenueNet`) in FY2022, ASC 606 tag in FY2023. |
| JUNIPER HOLDINGS | Foreign private issuer filing a 20-F: in `dim_company` and `dim_filing`, out of scope for the financial marts. |

Expected outputs derived from this data: [docs/EXPECTED_OUTPUTS.md](../../docs/EXPECTED_OUTPUTS.md).
