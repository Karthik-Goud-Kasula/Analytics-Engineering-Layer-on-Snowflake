/*
    SEC Analytics -- one-time Snowflake setup (development environment).

    WHO RUNS THIS: a user holding USERADMIN, SYSADMIN and SECURITYADMIN. In a
    trial account, the user who created the account has all three. ACCOUNTADMIN
    is NOT required, except for the optional resource monitor in step 6.

    WHAT IT CREATES (all new, all prefixed SEC_; nothing existing is touched):
        roles      SEC_LOADER, SEC_TRANSFORMER, SEC_ANALYST
        warehouse  SEC_TRANSFORM_WH (X-Small, suspends after 60 s idle)
        database   SEC_ANALYTICS_DEV, with schema RAW_SEC
        user       SEC_CI_SVC (service user for the optional GitHub workflow)

    Idempotent: every statement is CREATE ... IF NOT EXISTS or a GRANT, so it is
    safe to re-run.

    Least privilege:
        SEC_LOADER       owns RAW_SEC; can write raw files. Nothing else.
        SEC_TRANSFORMER  reads RAW_SEC; creates and owns the schemas dbt builds.
                         Cannot modify raw data.
        SEC_ANALYST      reads what dbt builds. Cannot read raw data.
*/

-- -------------------------------------------------------------------------
-- 1. Roles (USERADMIN owns roles and users)
-- -------------------------------------------------------------------------
use role useradmin;

create role if not exists sec_loader
    comment = 'SEC Analytics: loads raw SEC files into RAW_SEC';
create role if not exists sec_transformer
    comment = 'SEC Analytics: runs dbt; reads RAW_SEC, owns dbt schemas';
create role if not exists sec_analyst
    comment = 'SEC Analytics: read-only access to dbt models';

-- Roll the custom roles up to SYSADMIN so administrators can manage
-- everything they own (Snowflake's recommended role hierarchy).
grant role sec_loader to role sysadmin;
grant role sec_transformer to role sysadmin;
grant role sec_analyst to role sysadmin;

-- -------------------------------------------------------------------------
-- 2. Compute and storage (SYSADMIN owns warehouses and databases)
-- -------------------------------------------------------------------------
use role sysadmin;

create warehouse if not exists sec_transform_wh
    warehouse_size = xsmall
    auto_suspend = 60                     -- seconds idle before suspending
    auto_resume = true
    initially_suspended = true
    statement_timeout_in_seconds = 900    -- cost guard: kill runaway queries
    comment = 'SEC Analytics: loading and dbt transformations';

create database if not exists sec_analytics_dev
    comment = 'SEC Analytics: development (raw data + dbt output)';

create schema if not exists sec_analytics_dev.raw_sec
    comment = 'Raw SEC Financial Statement Data Sets, loaded as-is (all VARCHAR)';

grant usage on warehouse sec_transform_wh to role sec_loader;
grant usage on warehouse sec_transform_wh to role sec_transformer;
grant usage on warehouse sec_transform_wh to role sec_analyst;

grant usage on database sec_analytics_dev to role sec_loader;
grant usage on database sec_analytics_dev to role sec_transformer;
grant usage on database sec_analytics_dev to role sec_analyst;

-- dbt creates its own schemas: dbt_<you>_staging, ci_<run>_marts, ...
grant create schema on database sec_analytics_dev to role sec_transformer;

-- The transformer may read the raw schema...
grant usage on schema sec_analytics_dev.raw_sec to role sec_transformer;

-- ...which the loader owns. COPY CURRENT GRANTS keeps the usage grant above.
grant ownership on schema sec_analytics_dev.raw_sec
    to role sec_loader copy current grants;

-- -------------------------------------------------------------------------
-- 3. Future grants (need MANAGE GRANTS, which SECURITYADMIN holds)
-- -------------------------------------------------------------------------
use role securityadmin;

-- The transformer can SELECT every raw table, including ones created later.
grant select on all tables in schema sec_analytics_dev.raw_sec to role sec_transformer;
grant select on future tables in schema sec_analytics_dev.raw_sec to role sec_transformer;

-- The analyst can read every schema, table and view dbt creates. dbt replaces
-- tables with CREATE OR REPLACE, which creates a new object, so these future
-- grants apply on every rebuild; no grant hook is needed in dbt.
-- RAW_SEC is unaffected: it already exists (not a "future" schema), and its
-- schema-level future grant above takes precedence over database-level ones.
grant usage on future schemas in database sec_analytics_dev to role sec_analyst;
grant select on future tables in database sec_analytics_dev to role sec_analyst;
grant select on future views in database sec_analytics_dev to role sec_analyst;

-- -------------------------------------------------------------------------
-- 4. Your own user (for running the loader and dbt locally)
-- -------------------------------------------------------------------------
use role useradmin;

-- Replace <YOUR_USER> and run these two lines:
-- grant role sec_loader to user <YOUR_USER>;
-- grant role sec_transformer to user <YOUR_USER>;

-- Key-pair authentication (see README > Snowflake setup to generate the key),
-- then paste the public key body without the BEGIN/END lines:
-- alter user <YOUR_USER> set rsa_public_key = '<PUBLIC_KEY_BODY>';

-- -------------------------------------------------------------------------
-- 5. Service user for the optional GitHub Actions integration workflow
-- -------------------------------------------------------------------------
-- TYPE = SERVICE: no password and no interactive login; key-pair only.
create user if not exists sec_ci_svc
    type = service
    default_role = sec_transformer
    default_warehouse = sec_transform_wh
    comment = 'SEC Analytics: GitHub Actions integration workflow';

grant role sec_transformer to user sec_ci_svc;

-- Generate a SEPARATE key pair for CI, store the private key as the GitHub
-- secret SNOWFLAKE_PRIVATE_KEY, then:
-- alter user sec_ci_svc set rsa_public_key = '<CI_PUBLIC_KEY_BODY>';

-- -------------------------------------------------------------------------
-- 6. Optional cost guard (requires ACCOUNTADMIN)
-- -------------------------------------------------------------------------
-- use role accountadmin;
-- create resource monitor if not exists sec_dev_monitor
--     with credit_quota = 10
--     frequency = monthly
--     start_timestamp = immediately
--     triggers
--         on 80 percent do notify
--         on 100 percent do suspend;
-- alter warehouse sec_transform_wh set resource_monitor = sec_dev_monitor;
