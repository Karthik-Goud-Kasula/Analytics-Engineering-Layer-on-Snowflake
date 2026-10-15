# Expected outputs (sample data)

Built from the synthetic sample in [`data/sample/`](../data/sample/README.md).
The data is deterministic, so these results are exact: any difference means
something changed. `make test-e2e` checks the marts against the same spec
automatically; on Snowflake, [`snowflake/queries/verify_outputs.sql`](../snowflake/queries/verify_outputs.sql)
runs the key checks below.

All companies and figures are fictional.

## Build result

`make build` on the local target, and the same on Snowflake (`TARGET=dev`):

```
Done. PASS=160 WARN=2 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=162
```

Snowflake was verified on 2026-10-15, on the first run and on the incremental
re-run, with the test suite of that day (`PASS=158 ... TOTAL=160`). The two tests
added since, `assert_raw_sec_data_is_loaded` and the `grain_ordinal` check on
`stg_sec__numeric_facts`, both pass on the sample.

The two warnings are intended — they demonstrate warn-level data-quality tests:

| Test | Result | Why |
|---|---|---|
| `assert_numeric_values_parse` | WARN 1 | ELM's deliberately malformed value `12O5000` (letter O). Staging nulls it instead of failing the load; this test makes it visible. |
| `assert_balance_sheet_balances` | WARN 2 | HAZEL reports redeemable preferred stock as temporary equity, so assets ≠ liabilities + equity as modelled, in both years. |

## Row counts

| Model | Rows |
|---|---|
| `dim_company` | 10 |
| `dim_company_history` | 11 (BIRCH has 2 versions) |
| `dim_filing` | 40 |
| `fct_financial_facts` | 2,116 (= every staged fact) |
| `fct_company_financials_annual` | 15 |
| `fct_financial_ratios` | 15 |
| `fct_yoy_growth` | 15 |
| `fct_company_financials_quarterly` | 26 |
| `mart_sector_benchmarks` | 1 |
| `mart_restatements` | 1 |
| `mart_data_quality_summary` | 2 |

## `fct_company_financials_annual`

| Company | FY | Revenue | Net income | EPS | Total assets | Equity | Source |
|---|---|---|---|---|---|---|---|
| ALDER SOFTWARE INC | 2022 | 410,000,000 | 61,462,000 | 1.23 | 640,000,000 | 380,000,000 | 10-K |
| ALDER SOFTWARE INC | 2023 | 480,000,000 | 92,430,000 | 1.85 | 720,000,000 | 440,000,000 | 10-K |
| BIRCH CLOUD INC | 2023 | 260,000,000 | 28,756,000 | 0.96 | 410,000,000 | 180,000,000 | 10-K/A |
| CEDAR NETWORKS CORP | 2023 | 150,000,000 | 13,035,000 | 0.65 | 230,000,000 | 120,000,000 | 10-K |
| DOGWOOD LABS INC | 2022 | 0 | -37,500,000 | -2.50 | 60,000,000 | 33,000,000 | 10-K |
| DOGWOOD LABS INC | 2023 | 0 | -44,800,000 | -2.99 | 30,000,000 | -10,000,000 | 10-K |
| ELM COMMERCE INC | 2022 | 80,000,000 | 11,060,000 | 0.92 | 95,000,000 | 55,000,000 | 10-K |
| ELM COMMERCE INC | 2023 | 95,000,000 | 14,615,000 | 1.22 | 108,000,000 | 66,000,000 | 10-K |
| FIR MEDICAL DEVICES CORP | 2023 | 300,000,000 | 39,500,000 | 1.58 | 545,000,000 | 250,000,000 | 10-K |
| GROVE REGIONAL BANCORP | 2022 | 150,000,000 | 35,550,000 | 0.89 | 4,000,000,000 | 400,000,000 | 10-K |
| GROVE REGIONAL BANCORP | 2023 | 170,000,000 | 31,600,000 | 0.79 | 4,300,000,000 | 430,000,000 | 10-K |
| HAZEL BIOTHERAPEUTICS INC | 2022 | 8,000,000 | -123,000,000 | -2.05 | 450,000,000 | 295,000,000 | 10-K |
| HAZEL BIOTHERAPEUTICS INC | 2023 | 12,000,000 | -138,000,000 | -2.30 | 400,000,000 | 240,000,000 | 10-K |
| IRONWOOD SECURITY SOFTWARE INC | 2022 | 60,000,000 | 2,370,000 | 0.13 | 85,000,000 | 50,000,000 | 10-K |
| IRONWOOD SECURITY SOFTWARE INC | 2023 | 75,000,000 | 8,888,000 | 0.49 | 96,000,000 | 59,000,000 | 10-K |

What these rows prove:

* **ALDER 2023 revenue is 480,000,000**, from the ASC 606 tag — not 482,500,000
  from the `Revenues` tag it also reported. **ALDER 2022 is 410,000,000**, from
  its own FY2022 10-K — not the recast prior-year column (414,100,000) in the
  FY2023 10-K.
* **BIRCH** comes from its 10-K/A (restated) and shows its current name.
* **CEDAR 2023 revenue is 150,000,000**: the co-registrant row (141,000,000) and
  the segment rows (90,000,000 + 60,000,000) do not leak in, and the Part III
  10-K/A filed later did not replace the 10-K.
* **ELM 2023** exists even though its 10-K arrived a quarter late (`2024q2`).
* **EPS is populated** (it is reported in `USD/shares`).
* **JUNIPER** (20-F) has no row: out of scope for the financial marts.

## `fct_company_financials_quarterly` — derived Q4

ALDER, fiscal 2023:

| Quarter | Revenue | Net income | EPS | Derived |
|---|---|---|---|---|
| 1 | 110,400,000 | 21,259,000 | 0.43 | no |
| 2 | 115,200,000 | 22,183,000 | 0.44 | no |
| 3 | 120,000,000 | 23,108,000 | 0.46 | no |
| 4 | 134,400,000 | 25,880,000 | — | **yes** (480,000,000 − 345,600,000) |

No Q4 is derived for DOGWOOD 2023 (Q2 missing) or FIR fiscal 2024 (no 10-K yet).

## `fct_financial_ratios` (selected)

| Company | FY | Gross margin | Operating margin | ROE | Current ratio | Debt/equity |
|---|---|---|---|---|---|---|
| ALDER SOFTWARE INC | 2023 | 0.80 | 0.2479 | 0.2101 | 2.00 | 0.0909 |
| BIRCH CLOUD INC | 2023 | 0.64 | 0.1592 | 0.1598 | 1.33 | 0.5556 |
| DOGWOOD LABS INC | 2023 | NULL | NULL | **NULL** (negative equity) | 1.47 | NULL |
| GROVE REGIONAL BANCORP | 2023 | NULL | NULL | 0.0735 | NULL (bank) | NULL |

DOGWOOD's margins are NULL because revenue is zero: undefined, not zero.

## `mart_sector_benchmarks`

| Sector | FY | Companies | Median gross margin | P25 | P75 | Median operating margin | Median ROE |
|---|---|---|---|---|---|---|---|
| Services | 2023 | 6 | 0.70 | 0.64 | 0.75 | 0.1592 | 0.1598 |

Only one row: Services FY2022 has 4 companies and the other sectors fewer,
below the 5-company minimum.

## `mart_restatements`

| Company | Original | Amendment | Net income: original → restated | Change | Material | Days later |
|---|---|---|---|---|---|---|
| BIRCH CLOUD INC | 0009900002-24-000001 | 0009900002-24-000003 | 36,656,000 → 28,756,000 | −21.55% | yes | 107 |

CEDAR's Part III 10-K/A is not here: it re-filed no financial statements.

## `fct_yoy_growth`

| Company | FY | Revenue growth | Net income growth |
|---|---|---|---|
| ALDER SOFTWARE INC | 2023 | 0.1707 | 0.5039 |
| ELM COMMERCE INC | 2023 | 0.1875 | 0.3214 |
| GROVE REGIONAL BANCORP | 2023 | 0.1333 | −0.1111 |
| HAZEL BIOTHERAPEUTICS INC | 2023 | 0.5000 | NULL (prior year a loss) |
| IRONWOOD SECURITY SOFTWARE INC | 2023 | 0.2500 | 2.7502 |
| DOGWOOD LABS INC | 2023 | NULL (prior revenue 0) | NULL |

## `mart_data_quality_summary`

| FY | Annual reports | From amendments | Revenue coverage | Balance sheet coverage | Unbalanced | Analysis ready |
|---|---|---|---|---|---|---|
| 2022 | 6 | 0 | 1.0 | 1.0 | 1 | true |
| 2023 | 9 | 1 | 1.0 | 1.0 | 1 | true |

## Incremental check

`make incremental-check` ends with every mart table identical between the
incremental and full-refresh builds, and reports along the way that a
`filed_date` watermark would have skipped all **349** facts of the backfilled
`2023q1` quarter.
