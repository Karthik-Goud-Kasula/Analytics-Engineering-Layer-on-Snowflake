/*
    SEC Analytics -- raw landing objects. Run AFTER 01_account_setup.sql.

    WHO RUNS THIS: a user granted SEC_LOADER (the owner of RAW_SEC).

    Creates the file format, the internal stage and the four raw tables. Every
    source column is VARCHAR: nothing is cast at load time, so a malformed value
    can never make the load drop a row. Typing happens in dbt staging, where it
    is tested.

    The column lists must match ingestion/sec_layout.py (the loader maps file
    columns by name onto these). python_tests/test_sec_layout.py enforces that.
*/

use role sec_loader;
use warehouse sec_transform_wh;
use schema sec_analytics_dev.raw_sec;

-- The SEC files are tab-delimited, UTF-8, with a header row and no quoting.
create file format if not exists sec_tsv
    type = csv
    field_delimiter = '\t'
    skip_header = 1
    -- No quoting in the source: a literal " in a company name is a character.
    field_optionally_enclosed_by = none
    -- Backslashes are data, not escape characters.
    escape_unenclosed_field = none
    empty_field_as_null = true
    encoding = 'UTF8'
    comment = 'SEC Financial Statement Data Sets text files';

-- Internal stage: the loader PUTs files here, COPYs them, then removes them.
create stage if not exists sec_stage
    file_format = (format_name = 'sec_tsv')
    comment = 'Transient landing area for SEC files during a load';

create table if not exists sub (
    adsh varchar,
    cik varchar,
    name varchar,
    sic varchar,
    countryba varchar,
    stprba varchar,
    cityba varchar,
    zipba varchar,
    bas1 varchar,
    bas2 varchar,
    baph varchar,
    countryma varchar,
    stprma varchar,
    cityma varchar,
    zipma varchar,
    mas1 varchar,
    mas2 varchar,
    countryinc varchar,
    stprinc varchar,
    ein varchar,
    former varchar,
    changed varchar,
    afs varchar,
    wksi varchar,
    fye varchar,
    form varchar,
    period varchar,
    fy varchar,
    fp varchar,
    filed varchar,
    accepted varchar,
    prevrpt varchar,
    detail varchar,
    instance varchar,
    nciks varchar,
    aciks varchar,
    _source_quarter varchar comment 'SEC data set quarter, e.g. 2024q1',
    _source_file varchar comment 'Staged file the row was loaded from',
    _loaded_at timestamp_ltz comment 'When the loader wrote the row'
)
comment = 'SEC sub.txt, loaded as-is';

create table if not exists num (
    adsh varchar,
    tag varchar,
    version varchar,
    ddate varchar,
    qtrs varchar,
    uom varchar,
    segments varchar,
    coreg varchar,
    value varchar,
    footnote varchar,
    _source_quarter varchar comment 'SEC data set quarter, e.g. 2024q1',
    _source_file varchar comment 'Staged file the row was loaded from',
    _loaded_at timestamp_ltz comment 'When the loader wrote the row'
)
comment = 'SEC num.txt, loaded as-is';

create table if not exists tag (
    tag varchar,
    version varchar,
    custom varchar,
    abstract varchar,
    datatype varchar,
    iord varchar,
    crdr varchar,
    tlabel varchar,
    doc varchar,
    _source_quarter varchar comment 'SEC data set quarter, e.g. 2024q1',
    _source_file varchar comment 'Staged file the row was loaded from',
    _loaded_at timestamp_ltz comment 'When the loader wrote the row'
)
comment = 'SEC tag.txt, loaded as-is';

create table if not exists pre (
    adsh varchar,
    report varchar,
    line varchar,
    stmt varchar,
    inpth varchar,
    rfile varchar,
    tag varchar,
    version varchar,
    plabel varchar,
    negating varchar,
    _source_quarter varchar comment 'SEC data set quarter, e.g. 2024q1',
    _source_file varchar comment 'Staged file the row was loaded from',
    _loaded_at timestamp_ltz comment 'When the loader wrote the row'
)
comment = 'SEC pre.txt, loaded as-is';
