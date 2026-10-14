from ingestion import sample_data
from ingestion.sample_data import COMPANIES, FILINGS, SCENARIOS, build_dataset, main


def test_committed_sample_matches_generator():
    """data/sample/ must be exactly what the generator produces."""
    assert main(["--check"]) == 0


def test_generator_is_deterministic():
    assert build_dataset() == build_dataset()


def test_every_company_has_a_documented_scenario_and_fictional_cik():
    assert set(COMPANIES) == set(SCENARIOS)
    assert all(company.cik >= 9_900_000 for company in COMPANIES.values())


def test_accession_numbers_are_unique():
    adshs = [filing.adsh for filing in FILINGS]
    assert len(adshs) == len(set(adshs))


def test_num_grain_is_unique_in_every_quarter():
    grain = ("adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg")
    for quarter, files in build_dataset().items():
        keys = [tuple(row[c] for c in grain) for row in files["num.txt"]]
        assert len(keys) == len(set(keys)), quarter


def test_balance_sheets_balance_except_temporary_equity():
    for (company, _), year in sample_data.YEARS.items():
        statement = year.balance.statement()
        liabilities_and_equity = (
            statement["total_liabilities"] + statement["total_equity"]
            + statement.get("temporary_equity", 0)
        )  # fmt: skip
        assert statement["total_assets"] == liabilities_and_equity, company
