"""Shared fixtures for the Python tests (loader, sample data, built outputs)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SAMPLE_DIR = REPO_ROOT / "data" / "sample"


@pytest.fixture
def sample_quarter(tmp_path: Path) -> Path:
    """A writable copy of one sample quarter (2024q1)."""
    target = tmp_path / "sample" / "2024q1"
    shutil.copytree(SAMPLE_DIR / "2024q1", target)
    return target
