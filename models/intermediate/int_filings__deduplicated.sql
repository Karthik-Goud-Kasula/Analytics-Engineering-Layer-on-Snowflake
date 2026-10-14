/*
    One authoritative filing per (company, period end date, base form type).

    A 10-K followed by a 10-K/A is two filings for the same fiscal year. This
    model resolves them to one, without deleting either: mart_restatements reads
    the superseded filings directly to compare them.

    Resolution rules:

    1. Only filings that contain a primary financial statement compete. A
       "Part III" 10-K/A (filed to add executive-compensation disclosures) has
       no statements at all; letting it win would erase the company's numbers
       for that year.
    2. Among those, the most recently FILED wins. Filed date, not period date,
       because an amendment shares its original's period.
    3. Deterministic tiebreaks (acceptance time, then accession number) so the
       same input always produces the same output.

    The SEC's prevrpt flag ("subsequently amended") is deliberately NOT used to
    exclude filings: it is also set when the only amendment is a Part III
    amendment, so filtering on it would drop the one filing that has numbers.

    Scope: domestic periodic reports (10-K, 10-Q and their amendments).
    Transition reports (10-KT) and foreign filers (20-F, 40-F, 6-K) are out of
    scope for the financial marts but remain in dim_filing.
*/

with submissions as (

    select * from {{ ref('stg_sec__submissions') }}
    where base_form_type in ('10-K', '10-Q')

),

statement_lines as (

    select
        filing_id,
        count(*) as primary_statement_line_count
    from {{ ref('stg_sec__presentation') }}
    where
        statement_code in {{ primary_statement_codes() }}
        and not is_parenthetical
    group by filing_id

),

ranked as (

    select
        submissions.filing_id,
        submissions.company_id,
        submissions.period_end_date,
        submissions.base_form_type,
        submissions.form_type,
        submissions.is_amendment,
        submissions.fiscal_year,
        submissions.fiscal_period,
        submissions.filed_date,
        submissions.accepted_at,
        statement_lines.primary_statement_line_count,
        count(*) over (
            partition by
                submissions.company_id,
                submissions.period_end_date,
                submissions.base_form_type
        ) as competing_filing_count,
        row_number() over (
            partition by
                submissions.company_id,
                submissions.period_end_date,
                submissions.base_form_type
            order by
                submissions.filed_date desc,
                submissions.accepted_at desc,
                submissions.filing_id desc
        ) as filing_rank

    from submissions
    inner join statement_lines
        on submissions.filing_id = statement_lines.filing_id

)

select
    filing_id,
    company_id,
    period_end_date,
    base_form_type,
    form_type,
    is_amendment,
    fiscal_year,
    fiscal_period,
    filed_date,
    accepted_at,
    primary_statement_line_count,
    competing_filing_count > 1 as has_superseded_filings
from ranked
where filing_rank = 1
