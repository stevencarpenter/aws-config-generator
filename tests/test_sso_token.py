"""Tests for SSO token cache reader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from aws_config_gen.errors import DiscoveryDataError
from aws_config_gen.sso_token import (
    TokenExpiredError,
    TokenNotFoundError,
    load_sso_token,
)

SESSION_NAME = "my-sso-session"


def _cache_key(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()


def _write_token(cache_dir: Path, session: str, token_data: dict) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(session)}.json"
    path.write_text(json.dumps(token_data))
    return path


def _make_token(
    expires_at: datetime,
    access_token: str = "valid-token-abc123",
) -> dict:
    return {
        "startUrl": "https://d-123456.awsapps.com/start/#",
        "region": "us-west-2",
        "accessToken": access_token,
        "expiresAt": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "clientId": "client-id",
        "clientSecret": "client-secret",
        "registrationExpiresAt": "2099-01-01T00:00:00Z",
    }


def test_valid_token(tmp_path: Path):
    future = datetime.now(timezone.utc) + timedelta(hours=8)
    token_data = _make_token(future, access_token="my-access-token")
    _write_token(tmp_path, SESSION_NAME, token_data)

    result = load_sso_token(SESSION_NAME, cache_dir=tmp_path)
    assert result == "my-access-token"


def test_expired_token_raises(tmp_path: Path):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    token_data = _make_token(past)
    _write_token(tmp_path, SESSION_NAME, token_data)

    with pytest.raises(TokenExpiredError, match="expired at"):
        load_sso_token(SESSION_NAME, cache_dir=tmp_path)


def test_missing_cache_file_raises(tmp_path: Path):
    with pytest.raises(TokenNotFoundError, match="No cached token"):
        load_sso_token("nonexistent-session", cache_dir=tmp_path)


def test_malformed_json_raises(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{_cache_key(SESSION_NAME)}.json"
    path.write_text("{not valid json!!!")

    with pytest.raises(DiscoveryDataError, match="Malformed token cache"):
        load_sso_token(SESSION_NAME, cache_dir=tmp_path)


def test_missing_access_token_key_raises(tmp_path: Path):
    future = datetime.now(timezone.utc) + timedelta(hours=8)
    token_data = _make_token(future)
    del token_data["accessToken"]
    _write_token(tmp_path, SESSION_NAME, token_data)

    with pytest.raises(DiscoveryDataError, match="KeyError"):
        load_sso_token(SESSION_NAME, cache_dir=tmp_path)


def test_missing_expires_at_key_raises(tmp_path: Path):
    future = datetime.now(timezone.utc) + timedelta(hours=8)
    token_data = _make_token(future)
    del token_data["expiresAt"]
    _write_token(tmp_path, SESSION_NAME, token_data)

    with pytest.raises(DiscoveryDataError, match="KeyError"):
        load_sso_token(SESSION_NAME, cache_dir=tmp_path)


def test_default_cache_dir_used(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fake_home = tmp_path / "fakehome"
    cache_dir = fake_home / ".aws" / "sso" / "cache"
    future = datetime.now(timezone.utc) + timedelta(hours=8)
    token_data = _make_token(future, access_token="default-dir-token")
    _write_token(cache_dir, SESSION_NAME, token_data)

    monkeypatch.setattr(Path, "home", lambda: fake_home)

    result = load_sso_token(SESSION_NAME)
    assert result == "default-dir-token"


@pytest.mark.parametrize(
    "content",
    [
        json.dumps(["not", "an", "object"]),
        json.dumps({"accessToken": "t", "expiresAt": "not-a-timestamp"}),
        json.dumps({"accessToken": 123, "expiresAt": "2099-01-01T00:00:00Z"}),
    ],
)
def test_malformed_token_cache_raises_discovery_data_error(tmp_path: Path, content):
    path = tmp_path / f"{_cache_key(SESSION_NAME)}.json"
    path.write_text(content)

    with pytest.raises(DiscoveryDataError, match="Malformed token cache"):
        load_sso_token(SESSION_NAME, cache_dir=tmp_path)


def test_naive_expires_at_is_treated_as_utc(tmp_path: Path):
    future = datetime.now(timezone.utc) + timedelta(hours=8)
    token_data = _make_token(future)
    token_data["expiresAt"] = future.strftime("%Y-%m-%dT%H:%M:%S")
    _write_token(tmp_path, SESSION_NAME, token_data)

    assert load_sso_token(SESSION_NAME, cache_dir=tmp_path) == "valid-token-abc123"
