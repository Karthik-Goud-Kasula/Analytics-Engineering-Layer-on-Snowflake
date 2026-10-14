{{
    config(
        severity='error',
        warn_if='>0',
        error_if='>2000',
        store_failures=true
    )
}}

/*
    Assets = Liabilities + Equity, within a tolerance relative to total assets.

    Warns rather than errors at low volume: some companies legitimately fail
    the identity as modelled here -- redeemable preferred stock classified as
    temporary ("mezzanine") equity, or non-controlling interests outside parent
    equity. Blocking every build on those would teach people to ignore the
    test. Past 2,000 failures the tag mapping is broken, not the filers, and it
    becomes an error.

    Failing rows are stored (store_failures) so they can be inspected.
*/

select
    cik,
    company_name,
    fiscal_year,
    total_assets,
    total_liabilities,
    total_equity,
    total_assets - (total_liabilities + total_equity) as imbalance
from {{ ref('fct_company_financials_annual') }}
where balance_sheet_balances = false
