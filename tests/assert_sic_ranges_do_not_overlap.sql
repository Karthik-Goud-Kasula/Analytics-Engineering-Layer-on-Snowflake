/*
    The SIC seed is joined with BETWEEN. If two ranges overlap, every company in
    the overlap matches twice: dim_company_history duplicates its rows and every
    downstream join fans out, inflating totals with no error anywhere.

    The seed and this test are a package: neither is correct without the other.
    Returns each overlapping pair, plus any inverted range (low > high).
*/

select
    a.sic_low as range_a_low,
    a.sic_high as range_a_high,
    b.sic_low as range_b_low,
    b.sic_high as range_b_high
from {{ ref('sic_sector_mapping') }} as a
inner join {{ ref('sic_sector_mapping') }} as b
    on
        a.sic_low < b.sic_low
        and a.sic_high >= b.sic_low

union all

select
    sic_low as range_a_low,
    sic_high as range_a_high,
    null as range_b_low,
    null as range_b_high
from {{ ref('sic_sector_mapping') }}
where sic_low > sic_high
