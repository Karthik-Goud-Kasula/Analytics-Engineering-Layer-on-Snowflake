"""Where quarterly SEC files come from: the bundled synthetic sample, or sec.gov."""

from __future__ import annotations

import logging
import shutil
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ingestion.quarters import parse_quarter
from ingestion.sec_layout import LAYOUTS

LOG = logging.getLogger(__name__)

SEC_URL_TEMPLATE = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class QuarterNotPublished(Exception):
    """The SEC has not published this quarter yet (HTTP 404). Expected, not an error."""


def sample_quarters(sample_root: Path) -> list[str]:
    """Quarters available in the bundled sample data, oldest first."""
    found = []
    for child in sample_root.iterdir() if sample_root.exists() else []:
        if not child.is_dir():
            continue
        try:
            parse_quarter(child.name)
        except ValueError:
            continue
        found.append(child.name)
    return sorted(found, key=parse_quarter)


def sample_quarter_dir(sample_root: Path, quarter: str) -> Path:
    directory = sample_root / quarter
    if not directory.is_dir():
        available = ", ".join(sample_quarters(sample_root)) or "none"
        raise FileNotFoundError(f"no sample data for {quarter} in {sample_root} (available: {available})")
    return directory


def download_quarter(
    quarter: str,
    download_root: Path,
    user_agent: str,
    *,
    timeout: int = 120,
    retries: int = 3,
    backoff_seconds: float = 5.0,
) -> Path:
    """Download and extract one quarterly zip from sec.gov. Returns the extract dir.

    The SEC requires a User-Agent that identifies the requester (name and email)
    and throttles clients that exceed its fair-access rate; this makes a single
    request per quarter with backoff on 429/5xx.
    """
    parse_quarter(quarter)
    if not user_agent or "@" not in user_agent:
        raise ValueError(
            "SEC_USER_AGENT must identify you, e.g. 'Jane Doe jane@example.com' "
            "(required by https://www.sec.gov/os/accessing-edgar-data)"
        )

    download_root.mkdir(parents=True, exist_ok=True)
    zip_path = download_root / f"{quarter}.zip"
    url = SEC_URL_TEMPLATE.format(quarter=quarter)
    request = Request(url, headers={"User-Agent": user_agent})

    for attempt in range(1, retries + 1):
        try:
            LOG.info("Downloading %s (attempt %d/%d)", url, attempt, retries)
            partial = zip_path.with_suffix(".zip.part")
            with urlopen(request, timeout=timeout) as response, partial.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
            partial.replace(zip_path)
            break
        except HTTPError as exc:
            if exc.code == 404:
                raise QuarterNotPublished(f"{quarter} is not published yet ({url})") from exc
            if exc.code == 403:
                raise PermissionError(
                    f"sec.gov refused the request (HTTP 403) for {url}. Check that "
                    "SEC_USER_AGENT contains your name and email, and that you are "
                    "not exceeding the SEC's request-rate limit."
                ) from exc
            if exc.code in RETRYABLE_STATUS and attempt < retries:
                time.sleep(backoff_seconds * 2 ** (attempt - 1))
                continue
            raise
        except URLError:
            if attempt < retries:
                time.sleep(backoff_seconds * 2 ** (attempt - 1))
                continue
            raise

    extract_dir = download_root / quarter
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        members = set(archive.namelist())
        for layout in LAYOUTS:
            if layout.file_name not in members:
                raise FileNotFoundError(f"{zip_path} does not contain {layout.file_name}")
            # Extract by known name only: never trust paths inside an archive.
            with archive.open(layout.file_name) as src, (extract_dir / layout.file_name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    LOG.info("Extracted %s to %s", zip_path.name, extract_dir)
    return extract_dir
