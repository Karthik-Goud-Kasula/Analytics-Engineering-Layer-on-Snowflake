# SEC Analytics -- developer commands. Run `make help` for the list.
#
# TARGET selects the dbt target:  local (DuckDB, default, no credentials)
#                                  dev   (Snowflake, needs .env -- see .env.example)

TARGET ?= local
QUARTERS ?= latest

PYTHON ?= python
export DBT_PROFILES_DIR := $(CURDIR)/profiles
export DBT_TARGET := $(TARGET)

# Credentials in .env (Snowflake, SEC_USER_AGENT) are read by dbt and by the
# loader themselves, so make does not parse .env. TARGET always decides the
# target for make commands; DBT_TARGET in .env only affects direct dbt calls.

ifeq ($(TARGET),local)
  LOADER_TARGET := duckdb
else
  LOADER_TARGET := snowflake
endif

.DEFAULT_GOAL := help
.PHONY: help install deps sample-data check-sample-data load-sample load-sec seed build \
        run test unit-test fresh freshness compile docs docs-serve data-dictionary \
        check-docs lint lint-sql lint-python format test-python test-e2e \
        incremental-check check-snowflake-sql ci clean

help:  ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------
install:  ## Install Python dependencies (runtime + dev tools)
	$(PYTHON) -m pip install -r requirements-dev.txt

deps:  ## Install dbt packages (dbt_utils)
	dbt deps

# --- data --------------------------------------------------------------------
sample-data:  ## Regenerate the synthetic sample in data/sample/
	$(PYTHON) -m ingestion.sample_data

check-sample-data:  ## Fail if data/sample/ differs from the generator
	$(PYTHON) -m ingestion.sample_data --check

load-sample:  ## Load the synthetic sample into TARGET's raw schema
	$(PYTHON) -m ingestion.load_sec_data --target $(LOADER_TARGET) --source sample --quarters all

load-sec:  ## Load real SEC quarters (QUARTERS=2024q1 2024q2 | 2023q1:2024q4 | latest)
	$(PYTHON) -m ingestion.load_sec_data --target $(LOADER_TARGET) --source sec --quarters $(QUARTERS)

# --- dbt ---------------------------------------------------------------------
seed:  ## Load seed CSVs
	dbt seed --target $(TARGET)

build:  ## Seed, run and test everything in dependency order
	dbt build --target $(TARGET)

run:  ## Run models only (no tests)
	dbt run --target $(TARGET)

test:  ## Run data tests and unit tests
	dbt test --target $(TARGET)

unit-test:  ## Run dbt unit tests only
	dbt test --target $(TARGET) --select "test_type:unit"

fresh:  ## Full rebuild, including the incremental fact table
	dbt build --target $(TARGET) --full-refresh

freshness:  ## Check source freshness
	dbt source freshness --target $(TARGET)

compile:  ## Compile models and analyses to target/compiled/
	dbt compile --target $(TARGET)

docs:  ## Generate dbt docs (lineage graph, column docs)
	dbt docs generate --target $(TARGET)

docs-serve: docs  ## Generate and serve dbt docs at http://localhost:8080
	dbt docs serve --port 8080

data-dictionary:  ## Regenerate docs/DATA_DICTIONARY.md from the model YAML
	dbt parse --target $(TARGET) --quiet
	$(PYTHON) scripts/build_data_dictionary.py

check-docs:  ## Fail if a model is undocumented/untested or docs are stale
	dbt docs generate --target $(TARGET) --quiet
	$(PYTHON) scripts/check_project.py --catalog
	$(PYTHON) scripts/build_data_dictionary.py --check

# --- quality -----------------------------------------------------------------
lint: lint-python lint-sql  ## Lint Python and SQL

lint-python:  ## ruff: Python lint and formatting check
	ruff check ingestion scripts python_tests orchestration
	ruff format --check ingestion scripts python_tests orchestration

lint-sql:  ## sqlfluff: lint dbt models and tests (Snowflake dialect)
	sqlfluff lint models tests analyses

format:  ## Auto-fix Python formatting and SQL lint where safe
	ruff format ingestion scripts python_tests orchestration
	sqlfluff fix models tests analyses

test-python:  ## Python unit tests (loader, layouts, sample data)
	$(PYTHON) -m pytest python_tests --ignore=python_tests/test_expected_outputs.py

test-e2e:  ## Compare built local marts with the sample spec (after build)
	$(PYTHON) -m pytest python_tests/test_expected_outputs.py -v

incremental-check:  ## Prove incremental == full refresh (local, ~2 min)
	$(PYTHON) scripts/reconcile_incremental.py

check-snowflake-sql:  ## Compile for Snowflake offline and parse every statement
	$(PYTHON) scripts/check_snowflake_sql.py

# --- everything ----------------------------------------------------------------
ci:  ## Everything CI runs, locally and offline
	$(MAKE) lint-python test-python check-sample-data deps
	rm -f data/warehouse/sec_analytics.duckdb
	$(MAKE) load-sample build test-e2e check-docs lint-sql check-snowflake-sql incremental-check TARGET=local

clean:  ## Remove dbt artifacts and local warehouses
	dbt clean
	rm -rf data/warehouse
