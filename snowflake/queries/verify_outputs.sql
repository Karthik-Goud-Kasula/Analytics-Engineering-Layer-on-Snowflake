/*
    Verify a Snowflake build of the SAMPLE data against the expected results
    documented in docs/EXPECTED_OUTPUTS.md. Run as SEC_TRANSFORMER (or
    SEC_ANALYST) after `make load-sample TARGET=dev` and `make build TARGET=dev`.

    Replace DBT_YOURNAME with your DBT_SNOWFLAKE_SCHEMA value. Each query states
    its expected result; any other result means the build is wrong.
*/

use warehouse sec_transform_wh;
use database sec_analytics_dev;

-- 1. Row counts. Expected: 15 annual rows, 26 quarterly rows, 10 companies,
--    1 restatement, 1 sector benchmark, 2116 atomic facts.
select 'fct_company_financials_annual' as model, count(*) as row_count
from dbt_yourname_marts.fct_company_financials_annual
union all select 'fct_company_financials_quarterly', count(*) from dbt_yourname_marts.fct_company_financials_quarterly
union all select 'dim_company', count(*) from dbt_yourname_marts.dim_company
union all select 'mart_restatements', count(*) from dbt_yourname_marts.mart_restatements
union all select 'mart_sector_benchmarks', count(*) from dbt_yourname_marts.mart_sector_benchmarks
union all select 'fct_financial_facts', count(*) from dbt_yourname_marts.fct_financial_facts;

-- 2. Tag priority and as-reported values. Expected: ALDER 2022 revenue
--    410000000 and 2023 revenue 480000000 (not 482500000 from "Revenues",
--    not the 1%-recast comparative), both from the ASC 606 tag.
select fiscal_year, revenue, revenue_tag, net_income, eps_basic
from dbt_yourname_marts.fct_company_financials_annual
where company_name = 'ALDER SOFTWARE INC'
order by fiscal_year;

-- 3. Derived Q4. Expected: ALDER fiscal 2023 Q4 revenue 134400000, is_derived_q4 = true.
select fiscal_quarter, revenue, is_derived_q4, has_complete_quarters
from dbt_yourname_marts.fct_company_financials_quarterly
where company_name = 'ALDER SOFTWARE INC' and fiscal_year = 2023
order by fiscal_quarter;

-- 4. Restatement. Expected: one BIRCH row, net income 36656000 -> 28756000,
--    change rate about -0.2155, is_material_restatement = true.
select company_name, original_net_income, restated_net_income,
       round(net_income_change_rate, 4) as change_rate, is_material_restatement
from dbt_yourname_marts.mart_restatements;

-- 5. SCD2. Expected: BIRCH has 2 versions; only BIRCH CLOUD INC is current.
select version_number, company_name, valid_from, valid_to, is_current
from dbt_yourname_marts.dim_company_history
where cik = 9900002
order by version_number;
