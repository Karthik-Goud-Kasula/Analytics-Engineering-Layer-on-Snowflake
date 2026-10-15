# Troubleshooting

Problems grouped by where they show up. Commands assume the repository root and
an activated virtual environment.

## Setup

**`dbt: command not found` / `No module named ingestion`**
The virtual environment isn't active, or you're not in the repository root.
Every new terminal starts without it. Run `source .venv/bin/activate` (after
`python3 -m venv .venv && make install` the first time), then run commands from
the root. The loader is run as a module: `python -m ingestion.load_sec_data`.

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

**`N row(s) do not have M tab-separated fields (lines ...)`**
More than `--max-rejected-rows` (default 100) rows in one file are malformed,
so nothing was loaded. A few malformed rows are normal in real SEC files and
are handled without failing:
* A value containing a tab, which the SEC encloses in double quotes, is
  **repaired**: the log says `repaired N row(s) with a double-quoted value`.
  2026q1 `num.txt` has two such rows.
* Anything else (an unquoted tab, a line break inside a value) is **left out**
  and written, with its line number, to
  `data/raw/_clean/<quarter>/<file>.rejected.txt`. The log says
  `left out N malformed row(s)`.

Hundreds of bad rows usually mean a truncated download or a format change.
Delete `data/raw/<quarter>.zip` and reload. If it persists, look at the reported
lines before raising the limit (`python -m ingestion.load_sec_data ...
--max-rejected-rows 500`).

Older versions of the loader stopped at the first such row with `expected 10
tab-separated fields, found 11`. Pull the latest code.

**`raw tables [...] not found in RAW_SEC`**
The setup didn't complete. Run `snowflake/setup/00_setup_all_in_one.sql`
(see [Snowflake setup in Snowsight](#snowflake-setup-in-snowsight)).

**`... file has N rows but COPY loaded M`**
The row-count reconciliation failed and the quarter's load was rolled back; the
previous load of that quarter is untouched. Usually a file-format mismatch:
check the `SEC_TSV` file format matches `02_raw_tables.sql`.

**DuckDB: `Could not set lock on file ... sec_analytics.duckdb`**
Another process has the local warehouse open (often `dbt docs serve`, a SQL
client, or a second `dbt` command). Close it and rerun.

## Snowflake setup in Snowsight

**Errors about the *last* line of a script you pasted, e.g. `Role 'SEC_ANALYST' does not exist or not authorized` or `User 'SEC_CI_SVC' does not exist`, with "Your primary role ACCOUNTADMIN"**
Your editor ran only the statement under the cursor, which after pasting is the
last one, so nothing before it ran. Use `snowflake/setup/00_setup_all_in_one.sql`:
its main part is a single statement, so one **Run** executes all of it. For any
other multi-statement script, use **Run All** (Ctrl/Cmd+Shift+Enter), or click
into each statement and run them in order.

**`No active warehouse selected in the current session`**
The worksheet has no warehouse. Run `use warehouse sec_transform_wh;`, or create
it first with statement A of `00_setup_all_in_one.sql`. A new worksheet needs
this again. From dbt instead, the role lacks `USAGE` on the warehouse or
`SNOWFLAKE_WAREHOUSE` names a different one.

**`Cannot perform SELECT. This session does not have a current database`**
A query used a two-part name before `use database` ran. The queries in
`snowflake/queries/verify_outputs.sql` are fully qualified to avoid this.

**`User '<NAME>' does not exist or not authorized` for your own user**
Unquoted names are upper-cased, so a user whose name is stored in lowercase
doesn't match. Write it in double quotes exactly as `show users` shows it, e.g.
`"jane"`. Better, avoid typing it at all: `alter user set ...` with no name
changes the logged-in user.

## Snowflake connection

**`JWT token is invalid`**
Snowflake reached your account but rejected the key. Check, in this order:
1. **`SNOWFLAKE_USER` must be your *login name*, which can differ from your user
   name.** Accounts created with an email address typically have the email as
   login name (e.g. `JANE@EXAMPLE.COM`) and a different user name. Find it with:
   ```sql
   show users ->> select "name", "login_name" from $1 where "name" = current_user();
   ```
2. **The public key on your user must match your private key file.** Compare the
   fingerprint Snowflake stores with the one computed locally:
   ```sql
   declare
       stmt varchar default 'desc user "' || current_user() || '"';
       res resultset;
   begin
       execute immediate stmt;
       res := (select "property", "value" from table(result_scan(last_query_id()))
               where "property" in ('NAME', 'LOGIN_NAME', 'RSA_PUBLIC_KEY_FP'));
       return table(res);
   end;
   ```
   ```bash
   openssl rsa -in ~/.snowflake/snowflake_rsa_key.p8 -pubout -outform DER 2>/dev/null \
     | openssl dgst -sha256 -binary | openssl enc -base64
   ```
   `RSA_PUBLIC_KEY_FP` must be `SHA256:` followed by the local value. If not,
   re-attach: `alter user set rsa_public_key = '<key body>';` with the line from
   README > Running on Snowflake > Key pair. A rebuilt dev container or Codespace
   loses `~/.snowflake`, so regenerate and re-attach there.
3. Encrypted key: set `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE`. Unencrypted: leave it
   empty.

**`[Errno 2] No such file or directory: '...snowflake_rsa_key.p8'`**
`SNOWFLAKE_PRIVATE_KEY_PATH` in `.env` is wrong, still the example value, or
uses `~` (dbt does not expand it). Set the absolute path printed by
`echo "$HOME/.snowflake/snowflake_rsa_key.p8"`.

**`Could not connect to Snowflake backend`**
`SNOWFLAKE_ACCOUNT` is wrong. Use `orgname-accountname` from
`select current_organization_name() || '-' || current_account_name();`. Don't use
the account locator (e.g. `AB12345`) or the full URL.

**`Object 'SEC_ANALYTICS_DEV.RAW_SEC.SUB' does not exist or not authorized`**
Usually the role, not the object: dbt runs as `SEC_TRANSFORMER`, which reads the
raw tables through a future grant created by the setup script. If the raw tables
were created before that grant existed, run (as ACCOUNTADMIN)
`grant select on all tables in schema sec_analytics_dev.raw_sec to role sec_transformer;`.

**`Insufficient privileges to operate on database`** when dbt creates schemas
`SEC_TRANSFORMER` needs `CREATE SCHEMA` on the database; re-run the setup script.

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

**`FAIL 1 assert_raw_sec_data_is_loaded`, and everything else skipped**
The raw tables are empty: nothing has been loaded, or the last load failed and
you built anyway. Load first (`make load-sample` or `make load-sec`), then build.
Before this test existed, the same situation showed up as `None is not of type
'integer'` errors in `dbt_utils_equal_rowcount_...` tests (see DECISIONS.md #21).

**`WARN accepted_values_stg_sec__numeric_facts_grain_ordinal__False__1`**
Some facts share all eight columns of the SEC's documented key. Both rows are
kept, and the warning keeps that visible. Expected with real data: 2026q1 has
17 such pairs, all in one filing. See MODELING.md > The eight-column fact grain.

**`not_null_stg_sec__submissions_period_end_date` fails**
A 10-K or 10-Q has no usable `period`, and the annual and quarterly marts need
it. Other forms are exempt: two F-1 registration statements in 2026q1 have none,
although the SEC documents the field as required. Find the filing with
`select adsh, form, period from raw_sec.sub where try_to_date(period, 'YYYYMMDD') is null`.

**`row_count_matches_fct_financial_facts_...` fails**
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
