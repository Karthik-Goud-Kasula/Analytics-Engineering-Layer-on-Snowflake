"""SEC download handling, with sec.gov replaced by a fake urlopen."""

from __future__ import annotations

import io
import zipfile
from urllib.error import HTTPError

import pytest

from ingestion import sources

UA = "Test User test@example.com"


def fake_zip(members=("sub.txt", "num.txt", "tag.txt", "pre.txt")) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name in members:
            archive.writestr(name, "header\n")
    return buffer.getvalue()


def http_error(code):
    return HTTPError("https://www.sec.gov/x.zip", code, "error", {}, None)


def test_requires_identifying_user_agent(tmp_path):
    with pytest.raises(ValueError, match="SEC_USER_AGENT"):
        sources.download_quarter("2024q1", tmp_path, "")


def test_404_means_not_published(tmp_path, monkeypatch):
    def urlopen(request, timeout):
        raise http_error(404)

    monkeypatch.setattr(sources, "urlopen", urlopen)
    with pytest.raises(sources.QuarterNotPublished):
        sources.download_quarter("2026q4", tmp_path, UA)


def test_403_is_an_error_not_a_skip(tmp_path, monkeypatch):
    def urlopen(request, timeout):
        raise http_error(403)

    monkeypatch.setattr(sources, "urlopen", urlopen)
    with pytest.raises(PermissionError, match="User-Agent|USER_AGENT"):
        sources.download_quarter("2024q1", tmp_path, UA)


def test_retries_then_extracts_only_known_files(tmp_path, monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(request.get_header("User-agent"))
        if len(calls) == 1:
            raise http_error(503)
        return io.BytesIO(fake_zip(("sub.txt", "num.txt", "tag.txt", "pre.txt", "../evil.txt")))

    monkeypatch.setattr(sources, "urlopen", urlopen)
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    extract_dir = sources.download_quarter("2024q1", tmp_path, UA)

    assert calls == [UA, UA]
    assert sorted(p.name for p in extract_dir.iterdir()) == ["num.txt", "pre.txt", "sub.txt", "tag.txt"]
    assert not (tmp_path / "evil.txt").exists()


def test_zip_missing_a_file_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "urlopen", lambda request, timeout: io.BytesIO(fake_zip(("sub.txt",))))
    with pytest.raises(FileNotFoundError, match="num.txt"):
        sources.download_quarter("2024q1", tmp_path, UA)
