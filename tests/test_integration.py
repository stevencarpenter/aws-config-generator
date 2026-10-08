"""End-to-end integration tests with mocked SSO API."""

from __future__ import annotations

import hashlib
import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from aws_config_gen.cli import cli
from aws_config_gen.config_writer import BEGIN_MARKER, END_MARKER

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SESSION_NAME = "test-session"
SSO_REGION = "us-west-2"
CACHE_HASH = hashlib.sha1(SESSION_NAME.encode()).hexdigest()

ACCOUNTS = [
    {
        "accountId": "111111111111",
        "accountName": "Alpha Corp",
        "emailAddress": "alpha@example.com",
    },
    {
        "accountId": "222222222222",
        "accountName": "Beta Inc",
        "emailAddress": "beta@example.com",
    },
    {
        "accountId": "333333333333",
        "accountName": "Gamma LLC",
        "emailAddress": "gamma@example.com",
    },
]

ROLES_BY_ACCOUNT: dict[str, list[str]] = {
    "111111111111": ["ReadOnlyPlus", "AdministratorAccess"],
    "222222222222": ["ReadOnlyPlus"],
    "333333333333": ["ViewOnly"],
}

GENERATOR_CONFIG_DATA = {
    "sso_session": SESSION_NAME,
    "sso_start_url": "https://test.awsapps.com/start/#",
    "sso_region": SSO_REGION,
    "default_region": "us-west-2",
    "account_names": {"111111111111": "alpha"},
    "role_short_names": {
        "ReadOnlyPlus": "ro",
        "AdministratorAccess": "admin",
    },
    "skip": [],
}

MANUAL_CONFIG = """\
[profile manual-profile]
region = eu-west-1
output = json
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_token_cache(home_dir: Path) -> None:
    cache_dir = home_dir / ".aws" / "sso" / "cache"
    cache_dir.mkdir(parents=True)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    token_data = {
        "accessToken": "fake-access-token",
        "expiresAt": expires_at,
    }
    (cache_dir / f"{CACHE_HASH}.json").write_text(json.dumps(token_data))


def _write_generator_config(tmp_path: Path) -> Path:
    generator_config_path = tmp_path / "config.json"
    generator_config_path.write_text(json.dumps(GENERATOR_CONFIG_DATA))
    return generator_config_path


def _mock_urlopen(req, **_kwargs):
    url = req.full_url
    if "/assignment/accounts" in url and "roles" not in url:
        body = json.dumps({"accountList": ACCOUNTS}).encode()
    elif "/assignment/roles" in url:
        account_id = url.split("account_id=")[1].split("&")[0]
        role_list = [{"roleName": r} for r in ROLES_BY_ACCOUNT.get(account_id, [])]
        body = json.dumps({"roleList": role_list}).encode()
    else:
        msg = f"Unexpected URL: {url}"
        raise ValueError(msg)

    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda self: self
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def integration_env(tmp_path: Path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    _write_token_cache(fake_home)
    generator_config_path = _write_generator_config(tmp_path)
    config_path = tmp_path / "aws_config"
    return fake_home, generator_config_path, config_path


def test_full_pipeline_dry_run(capsys, integration_env):
    fake_home, generator_config_path, config_path = integration_env

    with (
        patch("aws_config_gen.sso_token.Path.home", return_value=fake_home),
        patch(
            "aws_config_gen.sso_client.urllib.request.urlopen",
            side_effect=_mock_urlopen,
        ),
    ):
        rc = cli(
            [
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(config_path),
                "--dry-run",
            ]
        )

    assert rc == 0
    out = capsys.readouterr().out

    # sso-session stanza
    assert f"[sso-session {SESSION_NAME}]" in out
    assert "sso_start_url = https://test.awsapps.com/start/#" in out
    assert f"sso_region = {SSO_REGION}" in out

    # Multi-role account gets name-role profiles
    assert "[profile alpha-admin]" in out
    assert "[profile alpha-ro]" in out

    # Single-role account with override → just the account short name
    assert "[profile beta-inc]" in out

    # Single-role account without override → lowercased, hyphenated
    assert "[profile gamma-llc]" in out

    # Verify account IDs appear
    assert "sso_account_id = 111111111111" in out
    assert "sso_account_id = 222222222222" in out
    assert "sso_account_id = 333333333333" in out

    # Config file should NOT have been written
    assert not config_path.exists()


def test_full_pipeline_writes_config(integration_env):
    fake_home, generator_config_path, config_path = integration_env

    # Seed with manual content
    config_path.write_text(MANUAL_CONFIG)

    with (
        patch("aws_config_gen.sso_token.Path.home", return_value=fake_home),
        patch(
            "aws_config_gen.sso_client.urllib.request.urlopen",
            side_effect=_mock_urlopen,
        ),
    ):
        rc = cli(
            [
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(config_path),
            ]
        )

    assert rc == 0
    content = config_path.read_text()

    # Managed block present with markers
    assert BEGIN_MARKER in content
    assert END_MARKER in content

    # Generated profiles are present
    assert "[profile alpha-admin]" in content
    assert "[profile alpha-ro]" in content
    assert "[profile beta-inc]" in content
    assert "[profile gamma-llc]" in content

    # Manual content preserved
    assert "[profile manual-profile]" in content
    assert "region = eu-west-1" in content
    assert "output = json" in content


def test_marker_based_merge_preserves_manual_content(integration_env):
    fake_home, generator_config_path, config_path = integration_env

    # Start with manual content AND a pre-existing managed block
    old_managed = (
        f"{BEGIN_MARKER}\n"
        "[sso-session old]\n"
        "sso_start_url = https://old.example.com\n"
        "sso_region = us-east-1\n"
        "sso_registration_scopes = sso:account:access\n"
        "\n"
        "[profile stale-profile]\n"
        "sso_session = old\n"
        "sso_account_id = 999999999999\n"
        "sso_role_name = OldRole\n"
        "region = us-east-1\n"
        f"{END_MARKER}\n"
    )
    existing = MANUAL_CONFIG + "\n" + old_managed

    config_path.write_text(existing)

    with (
        patch("aws_config_gen.sso_token.Path.home", return_value=fake_home),
        patch(
            "aws_config_gen.sso_client.urllib.request.urlopen",
            side_effect=_mock_urlopen,
        ),
    ):
        rc = cli(
            [
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(config_path),
            ]
        )

    assert rc == 0
    content = config_path.read_text()

    # Old managed content is gone
    assert "[profile stale-profile]" not in content
    assert "999999999999" not in content
    assert "[sso-session old]" not in content

    # New managed content is present
    assert f"[sso-session {SESSION_NAME}]" in content
    assert "[profile alpha-admin]" in content
    assert "[profile alpha-ro]" in content
    assert "[profile beta-inc]" in content
    assert "[profile gamma-llc]" in content

    # Manual profile survived the replacement
    assert "[profile manual-profile]" in content
    assert "region = eu-west-1" in content
    assert "output = json" in content

    # Exactly one managed block
    assert content.count(BEGIN_MARKER) == 1
    assert content.count(END_MARKER) == 1


def test_full_pipeline_multiple_identity_centers(tmp_path: Path):
    """Two Identity Centers in different regions, each with its own token."""
    fake_home = tmp_path / "home"
    cache_dir = fake_home / ".aws" / "sso" / "cache"
    cache_dir.mkdir(parents=True)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    for session in ("acme", "beta"):
        key = hashlib.sha1(session.encode()).hexdigest()
        (cache_dir / f"{key}.json").write_text(
            json.dumps({"accessToken": f"{session}-token", "expiresAt": expires_at})
        )

    generator_config_path = tmp_path / "config.json"
    generator_config_path.write_text(
        json.dumps(
            {
                "default_region": "us-west-2",
                "role_short_names": {"AdministratorAccess": "admin"},
                "identity_centers": [
                    {
                        "sso_session": "acme",
                        "sso_start_url": "https://acme.awsapps.com/start",
                        "sso_region": "us-east-1",
                    },
                    {
                        "sso_session": "beta",
                        "sso_start_url": "https://beta.awsapps.com/start",
                        "sso_region": "eu-west-1",
                        "profile_prefix": "beta",
                    },
                ],
            }
        )
    )
    config_path = tmp_path / "aws_config"
    config_path.write_text(MANUAL_CONFIG)

    # Each Identity Center (keyed by portal region + token) exposes its own accounts
    accounts_by_token = {
        ("us-east-1", "acme-token"): [ACCOUNTS[0]],
        ("eu-west-1", "beta-token"): [ACCOUNTS[1]],
    }

    def _urlopen(req, **_kwargs):
        region = req.full_url.split("portal.sso.")[1].split(".")[0]
        token = req.get_header("X-amz-sso_bearer_token")
        accounts = accounts_by_token[(region, token)]
        url = req.full_url
        if "/assignment/accounts" in url:
            body = {"accountList": accounts}
        else:
            account_id = url.split("account_id=")[1].split("&")[0]
            assert account_id in {a["accountId"] for a in accounts}
            body = {"roleList": [{"roleName": r} for r in ROLES_BY_ACCOUNT[account_id]]}
        resp = MagicMock()
        resp.read.return_value = json.dumps(body).encode()
        resp.__enter__ = lambda self: self
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    with (
        patch("aws_config_gen.sso_token.Path.home", return_value=fake_home),
        patch(
            "aws_config_gen.sso_client.urllib.request.urlopen",
            side_effect=_urlopen,
        ),
    ):
        rc = cli(
            [
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(config_path),
            ]
        )

    assert rc == 0
    content = config_path.read_text()

    assert content.count(BEGIN_MARKER) == 1
    assert (
        "[sso-session acme]\nsso_start_url = https://acme.awsapps.com/start" in content
    )
    assert (
        "[sso-session beta]\nsso_start_url = https://beta.awsapps.com/start" in content
    )
    assert "sso_region = us-east-1" in content
    assert "sso_region = eu-west-1" in content
    # acme: multi-role account, no prefix
    assert "[profile alpha-corp-admin]\nsso_session = acme" in content
    assert "[profile alpha-corp-readonlyplus]\nsso_session = acme" in content
    # beta: single-role account, prefixed
    assert "[profile beta-beta-inc]\nsso_session = beta" in content
    assert "[profile manual-profile]" in content
