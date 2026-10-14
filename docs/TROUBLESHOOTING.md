# Troubleshooting

Problems grouped by where they show up. Commands assume the repository root and
an activated virtual environment.

## Setup

**`dbt: command not found` / `No module named ingestion`**
The virtual environment isn't active, or you're not in the repository root.
`source .venv/bin/activate`, then run commands from the root (the loader is run
as a module: `python -m ingestion.load_sec_data`).

**`Could not find profile named 'sec_analytics'`**
dbt is looking in `~/.dbt/`. The profile lives in `profiles/`: use `make ...`,
which sets this for you, or `export DBT_PROFILES_DIR=profiles`.

**`Env var required but not provided: 'SNOWFLAKE_ACCOUNT'`**
You're running a Snowflake target without credentials. Either use the local
target (`make build`, or `TARGET=local`), or fill in `.env` from `.env.example`.

**`dbt deps` fails**
`packages.yml` installs dbt_utils from github.com. Check you can reach GitHub
(`git ls-remote https://github.com/dbt-labs/dbt-utils.git`), including through
any corporate proxy.

## Loading data

**Loader exits with code 99 and "Nothing new to load"**
Not an error. The quarter isn't published yet (the SEC returned 404), or it is
already loaded and you passed `--skip-if-loaded`. Airflow shows this as
*skipped*.

**`sec.gov refused the request (HTTP 403)`**
The SEC requires a User-Agent with your name and email. Set
`SEC_USER_AGENT="Your Name you@example.com"` in `.env`. Repeated 403s can also
mean you're exceeding the SEC's request-rate limit; wait and retry.

**`header does not match the SEC ... layout. Unknown columns: [...]`**
The SEC changed the file format. Add the column to `ingestion/sec_layout.py`
and to `snowflake/setup/02_raw_tables.sql` (a test checks they agree), then
decide whether staging should expose it.

**`expected N tab-separated fields, found M`**
A row contains an embedded tab or line break, or the file is truncated. The
load is refused rather than shifting values into the wrong columns. Re-download
the quarter; if it persists, inspect the reported line.

**`raw tables [...] not found in RAW_SEC`**
Run `snowflake/setup/02_raw_tables.sql` as a user holding `SEC_LOADER`.

**`... file has N rows but COPY loaded M`**
The row-count reconciliation failed and the quarter's load was rolled back; the
previous load of that quarter is untouched. Usually a file-format mismatch:
check the `SEC_TSV` file format matches `02_raw_tables.sql`.

**DuckDB: `Could not set lock on file ... sec_analytics.duckdb`**
Another process has the local warehouse open (often `dbt docs serve`, a SQL
client, or a second `dbt` command). Close it and rerun.

## Snowflake connection

**`JWT token is invalid` / `Failed to authenticate`**
* The public key isn't set on the user: `DESC USER <name>` should show
  `RSA_PUBLIC_KEY_FP`. Set it with `ALTER USER ... SET RSA_PUBLIC_KEY = '...'`
  (key body only, without the `-----BEGIN/END-----` lines).
* `SNOWFLAKE_PRIVATE_KEY_PATH` must be an **absolute** path; dbt does not
  expand `~`.
* Encrypted key: set `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`. Unencrypted: leave it
  empty.
* `SNOWFLAKE_ACCOUNT` is the account identifier (`orgname-accountname`), not
  the full URL.

**`Object 'SEC_ANALYTICS_DEV.RAW_SEC.SUB' does not exist or not authorized`**
Usually the role, not the object: dbt runs as `SEC_TRANSFORMER`, which needs
the future grant from `01_account_setup.sql` step 3. If the raw tables were
created before that grant existed, re-run the `grant select on all tables in
schema ...raw_sec` line from the same script.

**`Insufficient privileges to operate on database`** when dbt creates schemas
`SEC_TRANSFORMER` needs `CREATE SCHEMA` on the database (step 2 of the setup
script).

**`No active warehouse selected`**
The role lacks `USAGE` on `SEC_TRANSFORM_WH`, or `SNOWFLAKE_WAREHOUSE` names a
different warehouse.

## dbt build results

**`WARN 1 assert_numeric_values_parse` / `WARN 2 assert_balance_sheet_balances`**
Expected on the sample data — see [EXPECTED_OUTPUTS.md](EXPECTED_OUTPUTS.md).
Failing rows are stored for inspection:

```sql
select * from analytics_dbt_test__audit.assert_balance_sheet_balances;  -- local
select * from dbt_yourname_dbt_test__audit.assert_balance_sheet_balances;  -- Snowflake dev
```

**What happens when a test fails**
`dbt build` skips every model downstream of a failing `error`-severity test, so
nothing is built on data that failed validation. `warn` tests report and let
the build continue. Fix the cause, then `dbt retry` (re-runs only what failed or
was skipped) or rerun `make build`.

**`dbt_utils_equal_rowcount_fct_financial_facts_...` fails**
The incremental fact table no longer matches staging, usually because a
reloaded quarter dropped facts the table still holds. Rebuild it:
`dbt build --full-refresh --select fct_financial_facts` (or `make fresh`).

**`unique_stg_sec__submissions_filing_id` fails**
The same accession number exists in two loaded quarters. Loading the same
quarter twice cannot cause this (the loader replaces a quarter); check whether
a file was loaded under the wrong quarter name.

**`assert_sic_ranges_do_not_overlap` fails**
Someone edited `seeds/sic_sector_mapping.csv` and two ranges now overlap. Fix
the seed before anything else: every company in the overlap would be
duplicated downstream.

## Tooling

**`sqlfluff` fails with a dbt profile error**
The dbt templater compiles the project with the `local` target in
`profiles/profiles.yml`. Run it from the repository root (`make lint-sql`).

**`make check-docs` fails: "documented but not in the built table" / "column exists but is not documented"**
A model's columns and its YAML have drifted apart. Update the YAML, then
`make data-dictionary` to regenerate `docs/DATA_DICTIONARY.md`.

**`make check-sample-data` fails**
`data/sample/` no longer matches `ingestion/sample_data.py`. If you changed the
generator on purpose, run `make sample-data` and commit the result (and update
`docs/EXPECTED_OUTPUTS.md` if the numbers changed).
