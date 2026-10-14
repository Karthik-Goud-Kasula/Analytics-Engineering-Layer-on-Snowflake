#!/usr/bin/env python3
"""Project hygiene checks that dbt itself does not enforce.

    python scripts/check_project.py              # needs target/manifest.json (dbt parse)
    python scripts/check_project.py --catalog    # also needs target/catalog.json (dbt docs generate)

Checks:
  1. Every model has a description and at least one data test or unit test.
     An undocumented, untested model is one nobody can safely reuse.
  2. Every mart column is documented with a description and a data_type
     (they feed docs/DATA_DICTIONARY.md).
  3. With --catalog: the documented columns of each mart match the columns the
     built table really has -- no stale docs, no undocumented columns.
  4. profiles/profiles.yml contains no literal credentials: every credential
     field must be an env_var() lookup.

Exits 1 and lists every problem found.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CREDENTIAL_FIELDS = {
    "account", "user", "password", "private_key", "private_key_path",
    "private_key_passphrase", "token", "oauth_client_secret", "refresh_token",
}  # fmt: skip
ENV_VAR_ONLY = re.compile(r"^\s*\{\{\s*env_var\(\s*'[A-Z0-9_]+'\s*(,\s*'[^']*'\s*)?\)\s*\}\}\s*$")


def load_json(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"error: {path} not found. Run `dbt parse` (and `dbt docs generate` for --catalog).")
    return json.loads(path.read_text(encoding="utf-8"))


def check_models(manifest: dict) -> list[str]:
    problems = []
    tested: set[str] = set()
    for node in list(manifest["nodes"].values()) + list(manifest.get("unit_tests", {}).values()):
        if node["resource_type"] in ("test", "unit_test"):
            tested.update(n for n in node["depends_on"]["nodes"] if n.startswith("model."))

    for unique_id, node in manifest["nodes"].items():
        if node["resource_type"] != "model" or node["package_name"] != "sec_analytics":
            continue
        name = node["name"]
        if not node.get("description", "").strip():
            problems.append(f"{name}: model has no description")
        if unique_id not in tested:
            problems.append(f"{name}: model has no data tests or unit tests")
        if "/marts/" in node["original_file_path"]:
            for column, spec in node.get("columns", {}).items():
                if not spec.get("description", "").strip():
                    problems.append(f"{name}.{column}: mart column has no description")
                if not spec.get("data_type"):
                    problems.append(f"{name}.{column}: mart column has no data_type")
    return problems


def check_catalog(manifest: dict, catalog: dict) -> list[str]:
    problems = []
    for unique_id, node in manifest["nodes"].items():
        if node["resource_type"] != "model" or "/marts/" not in node["original_file_path"]:
            continue
        built = catalog["nodes"].get(unique_id)
        if built is None:
            problems.append(f"{node['name']}: not in catalog.json (was it built?)")
            continue
        actual = {c.lower() for c in built["columns"]}
        documented = {c.lower() for c in node.get("columns", {})}
        for column in sorted(actual - documented):
            problems.append(f"{node['name']}.{column}: column exists but is not documented")
        for column in sorted(documented - actual):
            problems.append(f"{node['name']}.{column}: documented but not in the built table")
    return problems


def check_profiles(path: Path) -> list[str]:
    problems = []
    profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    for profile_name, profile in profiles.items():
        for target_name, output in profile.get("outputs", {}).items():
            for field, value in output.items():
                if field in CREDENTIAL_FIELDS and not ENV_VAR_ONLY.match(str(value)):
                    problems.append(
                        f"{path.name}: {profile_name}.{target_name}.{field} must be an "
                        "env_var() lookup, not a literal value"
                    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", action="store_true", help="Also check catalog.json")
    args = parser.parse_args()

    manifest = load_json(REPO_ROOT / "target" / "manifest.json")
    problems = check_models(manifest)
    if args.catalog:
        problems += check_catalog(manifest, load_json(REPO_ROOT / "target" / "catalog.json"))
    problems += check_profiles(REPO_ROOT / "profiles" / "profiles.yml")

    if problems:
        print(f"{len(problems)} problem(s) found:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    models = sum(
        1 for n in manifest["nodes"].values()
        if n["resource_type"] == "model" and n["package_name"] == "sec_analytics"
    )  # fmt: skip
    print(f"OK: {models} models documented and tested; profiles contain no literal credentials.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
