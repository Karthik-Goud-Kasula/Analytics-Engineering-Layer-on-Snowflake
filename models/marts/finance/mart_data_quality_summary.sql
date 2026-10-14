/*
    Data coverage by fiscal year, for deciding whether a year is usable.

    Grain: one row per fiscal_year.

    Exists because "the pipeline ran successfully" and "the data is usable" are
    different claims. A year is analysis-ready when at least
    var('analysis_ready_min_coverage') of its annual reports have revenue and a
    balance sheet. Check is_analysis_ready before cross-company analysis.
*/

with annual as (

    select * from {{ ref('fct_company_financials_annual') }}

)

select
    fiscal_year,
    count(*) as annual_report_count,
    sum(case when is_from_amendment then 1 else 0 end) as amended_report_count,
    avg(case when has_revenue then 1.0 else 0.0 end) as revenue_coverage,
    avg(case when has_balance_sheet then 1.0 else 0.0 end) as balance_sheet_coverage,
    avg(case when has_cash_flow then 1.0 else 0.0 end) as cash_flow_coverage,
    sum(case when balance_sheet_balances is not null then 1 else 0 end)
        as balance_sheet_checkable_count,
    sum(case when balance_sheet_balances = false then 1 else 0 end)
        as unbalanced_balance_sheet_count,
    avg(case when has_revenue then 1.0 else 0.0 end) >= {{ var('analysis_ready_min_coverage') }}
    and avg(case when has_balance_sheet then 1.0 else 0.0 end) >= {{ var('analysis_ready_min_coverage') }}
        as is_analysis_ready
from annual
group by fiscal_year
