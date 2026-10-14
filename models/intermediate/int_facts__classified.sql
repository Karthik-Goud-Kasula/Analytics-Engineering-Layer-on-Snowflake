/*
    Facts that belong on a company's primary financial statements, each
    classified to exactly one statement.

    One row per fact_key. Covers every 10-K/10-Q filing, superseded ones
    included (is_authoritative_filing marks the winner from
    int_filings__deduplicated), because mart_restatements compares the two.

    Filters, and why each exists:

    * Consolidated only (no segments, no co-registrant). Segment rows break a
      total down; co-registrant rows belong to a subsidiary. Either one would
      double-count or replace the company's own figure.
    * Current period only: the fact's end date must match the filing's own
      period. A 10-K also reports the prior year as a comparative column, often
      recast. Taking each period from its own filing keeps one consistent
      source per row ("as reported"); restatements are surfaced separately.
      SEC rounds fact dates to the nearest month end, so the match allows 15
      days of slack for 52/53-week fiscal years.
    * Presented on a statement, not parenthetically. pre.txt says where a tag
      was rendered; parenthetical lines ("net of allowance of $x") are not line
      items. When a tag appears on more than one statement (net income on the
      income statement and the cash flow statement), an explicit priority picks
      one, so each fact is counted once.
    * Not abstract, and with a value that parsed.
*/

with filings as (

    select
        filing_id,
        company_id,
        form_type,
        base_form_type,
        is_amendment,
        period_end_date as filing_period_end_date,
        fiscal_year,
        fiscal_period,
        filed_date,
        accepted_at
    from {{ ref('stg_sec__submissions') }}
    where base_form_type in ('10-K', '10-Q')

),

authoritative as (

    select filing_id from {{ ref('int_filings__deduplicated') }}

),

statement_classification as (

    select
        filing_id,
        tag,
        tag_version,
        statement_code
    from {{ ref('stg_sec__presentation') }}
    where
        not is_parenthetical
        and statement_code in ('IS', 'BS', 'CF', 'CI', 'EQ')
    qualify row_number() over (
        partition by filing_id, tag, tag_version
        order by
            case statement_code
                when 'IS' then 1
                when 'BS' then 2
                when 'CF' then 3
                when 'CI' then 4
                when 'EQ' then 5
            end,
            report_number,
            line_number
    ) = 1

),

tags as (

    select
        tag,
        tag_version,
        is_custom,
        is_abstract
    from {{ ref('stg_sec__tags') }}

),

facts as (

    select
        fact_key,
        filing_id,
        tag,
        tag_version,
        period_end_date,
        duration_quarters,
        unit_of_measure,
        fact_value
    from {{ ref('stg_sec__numeric_facts') }}
    where
        is_consolidated
        and fact_value is not null

)

select
    facts.fact_key,
    facts.filing_id,
    filings.company_id,
    filings.form_type,
    filings.base_form_type,
    filings.is_amendment,
    filings.fiscal_year,
    filings.fiscal_period,
    filings.filed_date,
    filings.accepted_at,
    facts.tag,
    facts.tag_version,
    facts.period_end_date,
    facts.duration_quarters,
    case
        when facts.duration_quarters = 0 then 'instant'
        when facts.duration_quarters = 1 then 'quarter'
        when facts.duration_quarters = 4 then 'annual'
        else 'year_to_date'
    end as period_type,
    facts.unit_of_measure,
    facts.fact_value,
    statement_classification.statement_code,
    case statement_classification.statement_code
        when 'IS' then 'income_statement'
        when 'BS' then 'balance_sheet'
        when 'CF' then 'cash_flow'
        when 'CI' then 'comprehensive_income'
        when 'EQ' then 'equity'
    end as statement_name,
    -- Custom tags carry the accession number as their version; fall back to
    -- that rule if a tag is missing from the tag file.
    coalesce(tags.is_custom, facts.tag_version = facts.filing_id) as is_custom_tag,
    authoritative.filing_id is not null as is_authoritative_filing

from facts
inner join filings
    on facts.filing_id = filings.filing_id
inner join statement_classification
    on
        facts.filing_id = statement_classification.filing_id
        and facts.tag = statement_classification.tag
        and facts.tag_version = statement_classification.tag_version
left join tags
    on
        facts.tag = tags.tag
        and facts.tag_version = tags.tag_version
left join authoritative
    on facts.filing_id = authoritative.filing_id

where
    abs({{ dbt.datediff('filings.filing_period_end_date', 'facts.period_end_date', 'day') }}) <= 15
    and not coalesce(tags.is_abstract, false)
