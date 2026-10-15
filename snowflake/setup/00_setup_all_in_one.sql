/*
    SEC Analytics -- one-time Snowflake setup in ONE statement (recommended).

    This is the exact sequence used to set up and verify the project on a real
    Snowflake trial account on 2026-10-15. It does the work of
    01_account_setup.sql AND 02_raw_tables.sql.

    Why a single block: some Snowsight editors run only the statement under the
    cursor when you press Run. A multi-statement script then silently runs one
    line and stops, leaving the account half set up. Everything below the two
    warehouse lines is one Snowflake Scripting block, so a single Run executes
    all of it, or fails with an error that names the exact line.

    HOW TO RUN (role ACCOUNTADMIN, e.g. a trial account's admin user):
      1. Run statement A, then statement B (one at a time is fine).
      2. Click anywhere inside block C and press Run once. Expected result:
         one row, "SEC Analytics setup complete for user ...".
      3. Attach your public key with statement D (see README > Running on
         Snowflake for generating the key).

    Safe to re-run: everything is CREATE ... IF NOT EXISTS or a repeatable
    GRANT, except the four ownership transfers at the very end, which a
    failed run can never have reached.

    Difference from 01/02: roles, warehouse, database and the CI user end up
    owned by ACCOUNTADMIN instead of USERADMIN/SYSADMIN, because a block cannot
    switch roles. Privileges are identical: SEC_LOADER owns the raw layer,
    SEC_TRANSFORMER reads it and owns what dbt builds, SEC_ANALYST reads dbt
    output only. Prefer 01 + 02 (run with "Run All") if you want the textbook
    ownership model.
*/

-- A. The warehouse must exist and be selected before block C can run.
create warehouse if not exists sec_transform_wh
    warehouse_size = xsmall auto_suspend = 60 auto_resume = true
    initially_suspended = true statement_timeout_in_seconds = 900;

-- B.
use warehouse sec_transform_wh;

-- C. Everything else, as one statement.
declare
    -- Your own user, quoted so names with lowercase or special characters match.
    me varchar default '"' || current_user() || '"';
begin
    -- Roles, rolled up to SYSADMIN, and granted to you
    create role if not exists sec_loader comment = 'SEC Analytics: loads raw SEC files';
    create role if not exists sec_transformer comment = 'SEC Analytics: runs dbt';
    create role if not exists sec_analyst comment = 'SEC Analytics: read-only';
    grant role sec_loader to role sysadmin;
    grant role sec_transformer to role sysadmin;
    grant role sec_analyst to role sysadmin;
    execute immediate 'grant role sec_loader to user ' || me;
    execute immediate 'grant role sec_transformer to user ' || me;
    execute immediate 'grant role sec_analyst to user ' || me;

    -- Database and raw schema
    create database if not exists sec_analytics_dev;
    create schema if not exists sec_analytics_dev.raw_sec;
    grant usage on warehouse sec_transform_wh to role sec_loader;
    grant usage on warehouse sec_transform_wh to role sec_transformer;
    grant usage on warehouse sec_transform_wh to role sec_analyst;
    grant usage on database sec_analytics_dev to role sec_loader;
    grant usage on database sec_analytics_dev to role sec_transformer;
    grant usage on database sec_analytics_dev to role sec_analyst;
    grant create schema on database sec_analytics_dev to role sec_transformer;
    grant usage on schema sec_analytics_dev.raw_sec to role sec_transformer;

    -- Future grants: the transformer reads every raw table; the analyst reads
    -- every schema, table and view dbt creates (but not the raw layer).
    grant select on future tables in schema sec_analytics_dev.raw_sec to role sec_transformer;
    grant usage on future schemas in database sec_analytics_dev to role sec_analyst;
    grant select on future tables in database sec_analytics_dev to role sec_analyst;
    grant select on future views in database sec_analytics_dev to role sec_analyst;

    -- Service user for the optional GitHub Actions workflow (key-pair only)
    create user if not exists sec_ci_svc
        type = service default_role = sec_transformer default_warehouse = sec_transform_wh;
    grant role sec_transformer to user sec_ci_svc;

    -- Raw landing objects (same definitions as 02_raw_tables.sql)
    create file format if not exists sec_analytics_dev.raw_sec.sec_tsv
        type = csv field_delimiter = '\t' skip_header = 1
        field_optionally_enclosed_by = none escape_unenclosed_field = none
        empty_field_as_null = true encoding = 'UTF8';
    create stage if not exists sec_analytics_dev.raw_sec.sec_stage
        file_format = (format_name = 'sec_analytics_dev.raw_sec.sec_tsv');
    create table if not exists sec_analytics_dev.raw_sec.sub (
        adsh varchar, cik varchar, name varchar, sic varchar, countryba varchar,
        stprba varchar, cityba varchar, zipba varchar, bas1 varchar, bas2 varchar,
        baph varchar, countryma varchar, stprma varchar, cityma varchar, zipma varchar,
        mas1 varchar, mas2 varchar, countryinc varchar, stprinc varchar, ein varchar,
        former varchar, changed varchar, afs varchar, wksi varchar, fye varchar,
        form varchar, period varchar, fy varchar, fp varchar, filed varchar,
        accepted varchar, prevrpt varchar, detail varchar, instance varchar,
        nciks varchar, aciks varchar,
        _source_quarter varchar, _source_file varchar, _loaded_at timestamp_ltz);
    create table if not exists sec_analytics_dev.raw_sec.num (
        adsh varchar, tag varchar, version varchar, ddate varchar, qtrs varchar,
        uom varchar, segments varchar, coreg varchar, value varchar, footnote varchar,
        _source_quarter varchar, _source_file varchar, _loaded_at timestamp_ltz);
    create table if not exists sec_analytics_dev.raw_sec.tag (
        tag varchar, version varchar, custom varchar, abstract varchar, datatype varchar,
        iord varchar, crdr varchar, tlabel varchar, doc varchar,
        _source_quarter varchar, _source_file varchar, _loaded_at timestamp_ltz);
    create table if not exists sec_analytics_dev.raw_sec.pre (
        adsh varchar, report varchar, line varchar, stmt varchar, inpth varchar,
        rfile varchar, tag varchar, version varchar, plabel varchar, negating varchar,
        _source_quarter varchar, _source_file varchar, _loaded_at timestamp_ltz);

    -- Hand the raw layer to the loader role. Last, so a re-run after an
    -- earlier error never reaches these twice.
    grant ownership on all tables in schema sec_analytics_dev.raw_sec to role sec_loader copy current grants;
    grant ownership on file format sec_analytics_dev.raw_sec.sec_tsv to role sec_loader copy current grants;
    grant ownership on stage sec_analytics_dev.raw_sec.sec_stage to role sec_loader copy current grants;
    grant ownership on schema sec_analytics_dev.raw_sec to role sec_loader copy current grants;

    return 'SEC Analytics setup complete for user ' || me;
end;

-- D. Attach your public key to YOUR user (no name needed: it applies to the
--    logged-in user). Paste the key body without the BEGIN/END lines.
-- alter user set rsa_public_key = 'MIIBIjANBgkq...';
