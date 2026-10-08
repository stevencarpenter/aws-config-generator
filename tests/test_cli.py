"""Tests for the CLI module."""

from __future__ import annotations

import http.client
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from aws_config_gen.cli import cli
from aws_config_gen.errors import DiscoveryDataError
from aws_config_gen.sso_token import TokenExpiredError, TokenNotFoundError
from aws_config_gen.types import AccountRole, SSOAccount

_ACCOUNT = SSOAccount(
    account_id="111111111111",
    account_name="Acme Corp",
    email_address="acme@example.com",
)


def _write_generator_config(tmp_path, sample_identity_center):
    generator_config_path = tmp_path / "config.json"
    generator_config_path.write_text(
        json.dumps(
            {
                "sso_session": sample_identity_center.sso_session,
                "sso_start_url": sample_identity_center.sso_start_url,
                "sso_region": sample_identity_center.sso_region,
                "default_region": sample_identity_center.default_region,
                "account_names": sample_identity_center.account_names,
                "role_short_names": sample_identity_center.role_short_names,
                "skip": sample_identity_center.skip,
            }
        )
    )
    return generator_config_path


def test_dry_run_prints_to_stdout(capsys, tmp_path, sample_identity_center):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)
    roles = [AccountRole(account=_ACCOUNT, role_name="ReadOnlyPlus")]

    with patch("aws_config_gen.cli.discover_all_roles", return_value=roles):
        rc = cli(["--dry-run", "--generator-config", str(generator_config_path)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "[profile acme]" in captured.out
    assert "[sso-session test-session]" in captured.out


def test_strict_returns_one_on_token_expired(capsys, tmp_path, sample_identity_center):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=TokenExpiredError("expired"),
    ):
        rc = cli(["--strict", "--generator-config", str(generator_config_path)])

    assert rc == 1
    captured = capsys.readouterr()
    assert "aws sso login" in captured.err


def test_non_strict_returns_zero_on_token_not_found(
    capsys, tmp_path, sample_identity_center
):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=TokenNotFoundError("not found"),
    ):
        rc = cli(["--generator-config", str(generator_config_path)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "aws sso login" in captured.err


def test_default_generator_config_path_is_overrides_json(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    rc = cli([])
    assert rc == 1
    captured = capsys.readouterr()
    assert "overrides.json" in captured.err


def test_missing_generator_config_returns_one(capsys, tmp_path):
    rc = cli(["--generator-config", str(tmp_path / "nonexistent.json")])

    assert rc == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_malformed_generator_config_returns_one(capsys, tmp_path):
    bad_config = tmp_path / "bad.json"
    bad_config.write_text("{not valid json")

    rc = cli(["--generator-config", str(bad_config)])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Invalid" in captured.err


def test_invalid_skip_config_returns_one(capsys, tmp_path):
    bad_config = tmp_path / "bad-skip.json"
    bad_config.write_text(
        json.dumps(
            {
                "sso_session": "test-session",
                "sso_start_url": "https://example.com/start",
                "sso_region": "us-east-1",
                "default_region": "us-east-1",
                "skip": [["123"]],
            }
        )
    )

    rc = cli(["--generator-config", str(bad_config)])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Invalid generator config file" in captured.err
    assert "skip entry 0 must be" in captured.err


def test_missing_required_key_message_is_unquoted(capsys, tmp_path):
    bad_config = tmp_path / "missing-key.json"
    bad_config.write_text(
        json.dumps({"sso_start_url": "https://example.com/start", "sso_region": "x"})
    )

    rc = cli(["--generator-config", str(bad_config)])

    assert rc == 1
    err = capsys.readouterr().err
    assert err.rstrip().endswith(": missing required key 'sso_session'")


def test_duplicate_profile_names_return_one(capsys, tmp_path, sample_identity_center):
    generator_config_path = tmp_path / "config.json"
    generator_config_path.write_text(
        json.dumps(
            {
                "sso_session": sample_identity_center.sso_session,
                "sso_start_url": sample_identity_center.sso_start_url,
                "sso_region": sample_identity_center.sso_region,
                "default_region": sample_identity_center.default_region,
                "account_names": {
                    "111111111111": "prod",
                    "222222222222": "prod",
                },
                "role_short_names": sample_identity_center.role_short_names,
                "skip": sample_identity_center.skip,
            }
        )
    )
    roles = [
        AccountRole(account=_ACCOUNT, role_name="ReadOnly"),
        AccountRole(
            account=SSOAccount(
                account_id="222222222222",
                account_name="Acme Dev",
                email_address="acme-dev@example.com",
            ),
            role_name="ReadOnly",
        ),
    ]

    with patch("aws_config_gen.cli.discover_all_roles", return_value=roles):
        rc = cli(["--generator-config", str(generator_config_path)])

    assert rc == 1
    captured = capsys.readouterr()
    assert "Invalid generator config file" in captured.err
    assert "Duplicate profile names" in captured.err


def test_existing_manual_profile_collision_absorbed(
    capsys, tmp_path, sample_identity_center
):
    generator_config_path = tmp_path / "config.json"
    generator_config_path.write_text(
        json.dumps(
            {
                "sso_session": sample_identity_center.sso_session,
                "sso_start_url": sample_identity_center.sso_start_url,
                "sso_region": sample_identity_center.sso_region,
                "default_region": sample_identity_center.default_region,
                "account_names": {
                    "111111111111": "prod",
                },
                "role_short_names": sample_identity_center.role_short_names,
                "skip": sample_identity_center.skip,
            }
        )
    )
    config_path = tmp_path / "aws-config"
    config_path.write_text("[profile prod]\nregion = us-east-1\n")
    roles = [AccountRole(account=_ACCOUNT, role_name="ReadOnly")]

    with patch("aws_config_gen.cli.discover_all_roles", return_value=roles):
        rc = cli(
            [
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(config_path),
            ]
        )

    assert rc == 0
    captured = capsys.readouterr()
    assert "Absorbed" in captured.err
    # Manual profile replaced by generated one
    content = config_path.read_text()
    assert content.count("[profile prod]") == 1
    assert "sso_session = test-session" in content


def test_http_401_shows_login_message(capsys, tmp_path, sample_identity_center):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=urllib.error.HTTPError(
            url="https://example.com",
            code=401,
            msg="Unauthorized",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        ),
    ):
        rc = cli(["--generator-config", str(generator_config_path)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "aws sso login" in captured.err
    assert "401" in captured.err


def test_network_error_non_strict_returns_zero(
    capsys, tmp_path, sample_identity_center
):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=urllib.error.URLError("Name or service not known"),
    ):
        rc = cli(["--generator-config", str(generator_config_path)])

    assert rc == 0
    captured = capsys.readouterr()
    assert "Failed to reach" in captured.err


def test_network_error_strict_returns_one(capsys, tmp_path, sample_identity_center):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=urllib.error.URLError("Connection refused"),
    ):
        rc = cli(["--strict", "--generator-config", str(generator_config_path)])

    assert rc == 1


# --- Multiple Identity Centers ---


def _write_multi_config(tmp_path):
    path = tmp_path / "multi.json"
    path.write_text(
        json.dumps(
            {
                "default_region": "us-west-2",
                "identity_centers": [
                    {
                        "sso_session": "acme",
                        "sso_start_url": "https://acme.awsapps.com/start",
                        "sso_region": "us-east-1",
                        "profile_prefix": "acme",
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
    return path


def test_multi_identity_center_dry_run(capsys, tmp_path):
    path = _write_multi_config(tmp_path)

    def _discover(session, region, _skip):
        assert region == {"acme": "us-east-1", "beta": "eu-west-1"}[session]
        return [AccountRole(account=_ACCOUNT, role_name="ReadOnly")]

    with patch("aws_config_gen.cli.discover_all_roles", side_effect=_discover):
        rc = cli(["--dry-run", "--generator-config", str(path)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "[sso-session acme]" in out
    assert "[sso-session beta]" in out
    assert "[profile acme-acme-corp]\nsso_session = acme" in out
    assert "[profile beta-acme-corp]\nsso_session = beta" in out


def test_multi_identity_center_partial_failure_leaves_config_untouched(
    capsys, tmp_path
):
    path = _write_multi_config(tmp_path)
    config_path = tmp_path / "aws-config"
    original = "[profile manual]\nregion = us-east-1\n"
    config_path.write_text(original)

    def _discover(session, _region, _skip):
        if session == "beta":
            raise TokenExpiredError("expired")
        return [AccountRole(account=_ACCOUNT, role_name="ReadOnly")]

    with patch("aws_config_gen.cli.discover_all_roles", side_effect=_discover):
        rc = cli(["--generator-config", str(path), "--config", str(config_path)])

    assert rc == 0
    err = capsys.readouterr().err
    assert "aws sso login --sso-session beta" in err
    assert "aws sso login --sso-session acme" not in err
    assert "left unchanged" in err
    assert config_path.read_text() == original


def test_multi_identity_center_reports_every_failure_strict(capsys, tmp_path):
    path = _write_multi_config(tmp_path)

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=TokenNotFoundError("missing"),
    ):
        rc = cli(["--strict", "--generator-config", str(path)])

    assert rc == 1
    err = capsys.readouterr().err
    assert "aws sso login --sso-session acme" in err
    assert "aws sso login --sso-session beta" in err


def test_duplicate_sso_sessions_return_one(capsys, tmp_path):
    center = {
        "sso_session": "same",
        "sso_start_url": "https://x.awsapps.com/start",
        "sso_region": "us-east-1",
        "default_region": "us-east-1",
    }
    path = tmp_path / "dup.json"
    path.write_text(json.dumps({"identity_centers": [center, center]}))

    rc = cli(["--generator-config", str(path)])

    assert rc == 1
    assert "Duplicate sso_session names" in capsys.readouterr().err


def test_ignored_top_level_keys_warning_printed(capsys, tmp_path):
    path = tmp_path / "warn.json"
    path.write_text(
        json.dumps(
            {
                "sso_session": "old",
                "default_region": "us-east-1",
                "identity_centers": [
                    {
                        "sso_session": "acme",
                        "sso_start_url": "https://acme.awsapps.com/start",
                        "sso_region": "us-east-1",
                    }
                ],
            }
        )
    )

    with patch("aws_config_gen.cli.discover_all_roles", return_value=[]):
        rc = cli(["--dry-run", "--generator-config", str(path)])

    assert rc == 0
    assert "[aws-config-gen] Warning: top-level sso_session ignored" in (
        capsys.readouterr().err
    )


def test_invalid_session_name_returns_one(capsys, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "sso_session": "my sso",
                "sso_start_url": "https://x.awsapps.com/start",
                "sso_region": "us-east-1",
                "default_region": "us-east-1",
            }
        )
    )

    rc = cli(["--generator-config", str(path)])

    assert rc == 1
    assert "sso_session must not contain whitespace" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("timed out"), "Failed to reach AWS Identity Center"),
        (ConnectionResetError("reset"), "Failed to reach AWS Identity Center"),
        (http.client.IncompleteRead(b""), "Failed to reach AWS Identity Center"),
        (DiscoveryDataError("bad token cache"), "Unexpected data"),
    ],
)
def test_other_discovery_errors_are_reported(
    capsys, tmp_path, sample_identity_center, exc, expected
):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)
    config_path = tmp_path / "aws-config"
    config_path.write_text("[profile manual]\n")

    with patch("aws_config_gen.cli.discover_all_roles", side_effect=exc):
        rc = cli(
            [
                "--strict",
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(config_path),
            ]
        )

    assert rc == 1
    err = capsys.readouterr().err
    assert expected in err
    assert "Traceback" not in err
    assert config_path.read_text() == "[profile manual]\n"


def test_unexpected_data_error_suggests_login(capsys, tmp_path, sample_identity_center):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with patch(
        "aws_config_gen.cli.discover_all_roles", side_effect=DiscoveryDataError("bad")
    ):
        cli(["--generator-config", str(generator_config_path)])

    err = capsys.readouterr().err
    assert f"aws sso login --sso-session {sample_identity_center.sso_session}" in err


def test_unexpected_exceptions_are_not_swallowed(tmp_path, sample_identity_center):
    """Bugs (e.g. a stray KeyError) must surface instead of looking like bad data."""
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)

    with (
        patch("aws_config_gen.cli.discover_all_roles", side_effect=KeyError("bug")),
        pytest.raises(KeyError),
    ):
        cli(["--generator-config", str(generator_config_path)])


def test_config_with_injected_lines_is_rejected(capsys, tmp_path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "sso_session": "acme",
                "sso_start_url": "https://acme.awsapps.com/start",
                "sso_region": "us-east-1",
                "default_region": "us-west-2\ncredential_process = /bin/echo pwned",
            }
        )
    )
    config_path = tmp_path / "aws-config"

    with patch("aws_config_gen.cli.discover_all_roles") as discover:
        rc = cli(["--generator-config", str(path), "--config", str(config_path)])

    assert rc == 1
    assert "must not contain line breaks" in capsys.readouterr().err
    discover.assert_not_called()
    assert not config_path.exists()


def test_single_identity_center_failure_says_config_unchanged(
    capsys, tmp_path, sample_identity_center
):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)
    config_path = tmp_path / "aws-config"
    config_path.write_text("[profile manual]\n")

    with patch(
        "aws_config_gen.cli.discover_all_roles",
        side_effect=TokenExpiredError("expired"),
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
    err = capsys.readouterr().err
    assert (
        f"Discovery failed for sso-session(s): {sample_identity_center.sso_session}; "
        "AWS config left unchanged."
    ) in err
    assert config_path.read_text() == "[profile manual]\n"


def test_unknown_key_warning_printed(capsys, tmp_path, sample_identity_center):
    path = _write_generator_config(tmp_path, sample_identity_center)
    data = json.loads(path.read_text())
    data["sso_sesion"] = "typo"
    path.write_text(json.dumps(data))

    with patch("aws_config_gen.cli.discover_all_roles", return_value=[]):
        cli(["--dry-run", "--generator-config", str(path)])

    assert (
        "[aws-config-gen] Warning: unknown key 'sso_sesion' ignored; "
        "did you mean 'sso_session'?"
    ) in capsys.readouterr().err


def test_write_os_error_is_reported_without_traceback(
    capsys, tmp_path, sample_identity_center
):
    generator_config_path = _write_generator_config(tmp_path, sample_identity_center)
    roles = [AccountRole(account=_ACCOUNT, role_name="ReadOnlyPlus")]

    with (
        patch("aws_config_gen.cli.discover_all_roles", return_value=roles),
        patch(
            "aws_config_gen.cli.write_config",
            side_effect=PermissionError(13, "Permission denied"),
        ),
    ):
        rc = cli(
            [
                "--generator-config",
                str(generator_config_path),
                "--config",
                str(tmp_path / "aws-config"),
            ]
        )

    assert rc == 1
    err = capsys.readouterr().err
    assert "Failed to write AWS config" in err
    assert "Permission denied" in err
