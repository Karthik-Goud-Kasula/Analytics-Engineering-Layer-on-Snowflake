.PHONY: help install deps seed run test build docs clean fresh lint check-docs load-data setup-snowflake

TARGET ?= dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install python dependencies
	pip install -r requirements.txt

deps:  ## Install dbt packages
	dbt deps

seed:  ## Load seed CSVs
	dbt seed --target $(TARGET)

run:  ## Run all models
	dbt run --target $(TARGET)

test:  ## Run all tests
	dbt test --target $(TARGET)

build:  ## Seed, run, test and snapshot in dependency order
	dbt build --target $(TARGET)

fresh:  ## Full refresh of incremental models
	dbt build --target $(TARGET) --full-refresh

staging:  ## Build the staging layer only
	dbt build --target $(TARGET) --select staging

marts:  ## Build marts and everything they depend on
	dbt build --target $(TARGET) --select +marts

docs:  ## Generate and serve documentation with lineage
	dbt docs generate --target $(TARGET)
	dbt docs serve --port 8080

freshness:  ## Check source freshness
	dbt source freshness --target $(TARGET)

lint:  ## Lint SQL with sqlfluff
	sqlfluff lint models tests --dialect snowflake

fix:  ## Auto-fix lint violations
	sqlfluff fix models tests --dialect snowflake

check-docs:  ## Fail if any model is undocumented or untested
	python scripts/check_model_documentation.py

load-data:  ## Download and load SEC data (YEARS=2022:2024)
	python scripts/load_sec_data.py --from-year $(word 1,$(subst :, ,$(YEARS))) --to-year $(word 2,$(subst :, ,$(YEARS)))

setup-snowflake:  ## Print the Snowflake setup scripts to run
	@echo "Run these in a Snowflake worksheet, in order:"
	@echo "  1. snowflake/setup/01_account_setup.sql  (as ACCOUNTADMIN)"
	@echo "  2. snowflake/setup/02_raw_tables.sql     (as LOADER)"

clean:  ## Remove dbt artifacts
	dbt clean
	rm -rf logs target dbt_packages
