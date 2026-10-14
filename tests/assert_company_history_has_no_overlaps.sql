/*
    SCD2 integrity for dim_company_history: a company's versions must chain
    with no gaps and no overlaps -- each version's valid_to equals the next
    version's valid_from -- and only the final version may be open-ended.
    Overlaps would make a point-in-time join return two rows (fan-out); gaps
    would make it return none.
*/

with ordered as (

    select
        cik,
        version_number,
        valid_from,
        valid_to,
        is_current,
        lead(valid_from) over (partition by cik order by valid_from) as next_valid_from
    from {{ ref('dim_company_history') }}

)

select *
from ordered
where
    (next_valid_from is not null and (valid_to is null or valid_to != next_valid_from))
    or (next_valid_from is null and (valid_to is not null or not is_current))
    or valid_to <= valid_from
