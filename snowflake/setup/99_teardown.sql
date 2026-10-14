/*
    SEC Analytics -- remove everything 01_account_setup.sql created.

    DESTRUCTIVE. Drops the SEC_ANALYTICS_DEV database (raw data and every dbt
    schema), the warehouse, the roles and the CI service user. Every statement
    is commented out so that running this file by accident does nothing:
    uncomment deliberately, after reading each line.

    Only SEC_-prefixed objects created by this project are named here.
*/

-- use role sysadmin;
-- drop database if exists sec_analytics_dev;
-- drop warehouse if exists sec_transform_wh;

-- use role useradmin;
-- drop user if exists sec_ci_svc;
-- drop role if exists sec_analyst;
-- drop role if exists sec_transformer;
-- drop role if exists sec_loader;

-- Only if you created it (requires ACCOUNTADMIN):
-- use role accountadmin;
-- drop resource monitor if exists sec_dev_monitor;
