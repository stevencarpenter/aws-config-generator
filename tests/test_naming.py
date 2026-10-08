"""Tests for profile naming logic."""

from __future__ import annotations

import json
import re

import pytest

from aws_config_gen.naming import build_profile_entries, load_generator_config
from aws_config_gen.types import (
    AccountRole,
    GeneratorConfig,
    IdentityCenterConfig,
    SSOAccount,
)


def _build(roles: list[AccountRole], config: GeneratorConfig):
    """Build entries for a single-Identity-Center config."""
    (center,) = config.identity_centers
    return build_profile_entries({center.sso_session: roles}, config)


def _center(**kwargs: object) -> IdentityCenterConfig:
    defaults: dict[str, object] = {
        "sso_session": "test-session",
        "sso_start_url": "https://example.com/start",
        "sso_region": "us-east-1",
        "default_region": "us-west-2",
        "account_names": {},
        "role_short_names": {},
        "skip": [],
    }
    defaults.update(kwargs)
    return IdentityCenterConfig(**defaults)  # type: ignore[arg-type]


def test_account_name_override_applied(sample_generator_config, sample_accounts):
    acme = sample_accounts[0]  # 111111111111 -> overridden to "acme"
    roles = [AccountRole(account=acme, role_name="ReadOnly")]

    entries = _build(roles, sample_generator_config)

    assert entries[0].profile_name == "acme"


def test_account_name_fallback(sample_generator_config, sample_accounts):
    other = sample_accounts[2]  # 333333333333 -> "Other Account" -> "other-account"
    roles = [AccountRole(account=other, role_name="ReadOnly")]

    entries = _build(roles, sample_generator_config)

    assert entries[0].profile_name == "other-account"


def test_role_short_name_override_applied(sample_generator_config, sample_accounts):
    acme = sample_accounts[0]
    roles = [
        AccountRole(account=acme, role_name="ReadOnlyPlus"),
        AccountRole(account=acme, role_name="AdministratorAccess"),
    ]

    entries = _build(roles, sample_generator_config)

    names = {e.profile_name for e in entries}
    assert "acme-ro" in names
    assert "acme-admin" in names


def test_role_short_name_fallback(sample_generator_config, sample_accounts):
    acme = sample_accounts[0]
    roles = [
        AccountRole(account=acme, role_name="ReadOnly"),
        AccountRole(account=acme, role_name="PowerUser"),
    ]

    entries = _build(roles, sample_generator_config)

    names = {e.profile_name for e in entries}
    assert "acme-readonly" in names
    assert "acme-poweruser" in names


def test_single_role_account_omits_suffix(sample_generator_config, sample_accounts):
    dev = sample_accounts[1]  # 222222222222 -> overridden to "acme-dev"
    roles = [AccountRole(account=dev, role_name="ReadOnly")]

    entries = _build(roles, sample_generator_config)

    assert entries[0].profile_name == "acme-dev"


def test_multi_role_account_includes_suffix(sample_generator_config, sample_accounts):
    other = sample_accounts[2]
    roles = [
        AccountRole(account=other, role_name="ReadOnly"),
        AccountRole(account=other, role_name="Admin"),
    ]

    entries = _build(roles, sample_generator_config)

    assert entries[0].profile_name == "other-account-admin"
    assert entries[1].profile_name == "other-account-readonly"


def test_alphabetical_sorting(sample_generator_config, sample_accounts):
    roles = [
        AccountRole(account=sample_accounts[2], role_name="ReadOnly"),  # other-account
        AccountRole(account=sample_accounts[0], role_name="ReadOnly"),  # acme
        AccountRole(account=sample_accounts[1], role_name="ReadOnly"),  # acme-dev
    ]

    entries = _build(roles, sample_generator_config)

    assert [e.profile_name for e in entries] == ["acme", "acme-dev", "other-account"]


def test_profile_entry_fields(sample_generator_config, sample_accounts):
    acme = sample_accounts[0]
    roles = [AccountRole(account=acme, role_name="ReadOnly")]

    entries = _build(roles, sample_generator_config)

    entry = entries[0]
    assert entry.sso_session == "test-session"
    assert entry.account_id == "111111111111"
    assert entry.role_name == "ReadOnly"
    assert entry.region == "us-west-2"


def test_duplicate_profile_names_raise_error(sample_accounts):
    config = GeneratorConfig(
        identity_centers=[
            _center(account_names={"111111111111": "prod", "222222222222": "prod"})
        ]
    )
    roles = [
        AccountRole(account=sample_accounts[0], role_name="ReadOnly"),
        AccountRole(account=sample_accounts[1], role_name="ReadOnly"),
    ]

    with pytest.raises(ValueError, match="Duplicate profile names"):
        _build(roles, config)


def test_load_generator_config(tmp_path):
    data = {
        "sso_session": "test-session",
        "sso_start_url": "https://example.com/start",
        "sso_region": "us-east-1",
        "default_region": "us-east-1",
        "account_names": {"123": "myacct"},
        "role_short_names": {"AdminRole": "admin"},
        "skip": [["123", "AdminRole"]],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))

    result = load_generator_config(path)

    assert isinstance(result, GeneratorConfig)
    (center,) = result.identity_centers
    assert center.sso_session == "test-session"
    assert center.sso_start_url == "https://example.com/start"
    assert center.sso_region == "us-east-1"
    assert center.default_region == "us-east-1"
    assert center.account_names == {"123": "myacct"}
    assert center.role_short_names == {"AdminRole": "admin"}
    assert center.skip == [("123", "AdminRole")]
    assert center.profile_prefix == ""


def test_load_generator_config_rejects_bad_skip_entry(tmp_path):
    data = {
        "sso_session": "s",
        "sso_start_url": "https://example.com",
        "sso_region": "us-east-1",
        "default_region": "us-east-1",
        "skip": [["only-one-element"]],
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))

    with pytest.raises(ValueError, match="skip entry 0 must be"):
        load_generator_config(path)


# --- Multiple Identity Centers ---


def _write_json(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data))
    return path


def test_load_multi_identity_center_config_with_shared_defaults(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-west-2",
            "role_short_names": {"AdministratorAccess": "admin", "ReadOnly": "ro"},
            "account_names": {"111": "shared-name"},
            "skip": [["111", "Unused"]],
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
                    "default_region": "eu-central-1",
                    "role_short_names": {"ReadOnly": "view"},
                    "account_names": {"222": "beta-prod"},
                    "skip": [["222", "Other"]],
                },
            ],
        },
    )

    acme, beta = load_generator_config(path).identity_centers

    assert acme.sso_session == "acme"
    assert acme.profile_prefix == "acme"
    assert acme.default_region == "us-west-2"
    assert acme.role_short_names == {"AdministratorAccess": "admin", "ReadOnly": "ro"}
    assert acme.skip == [("111", "Unused")]

    assert beta.sso_region == "eu-west-1"
    assert beta.default_region == "eu-central-1"
    assert beta.profile_prefix == ""
    # Per-entry values override/extend the shared ones
    assert beta.role_short_names == {"AdministratorAccess": "admin", "ReadOnly": "view"}
    assert beta.account_names == {"111": "shared-name", "222": "beta-prod"}
    assert beta.skip == [("111", "Unused"), ("222", "Other")]


def test_load_multi_identity_center_rejects_duplicate_sessions(tmp_path):
    center = {
        "sso_session": "same",
        "sso_start_url": "https://x.awsapps.com/start",
        "sso_region": "us-east-1",
        "default_region": "us-east-1",
    }
    path = _write_json(tmp_path, {"identity_centers": [center, center]})

    with pytest.raises(ValueError, match="Duplicate sso_session names"):
        load_generator_config(path)


def test_load_multi_identity_center_rejects_empty_list(tmp_path):
    path = _write_json(tmp_path, {"identity_centers": []})

    with pytest.raises(ValueError, match="non-empty list"):
        load_generator_config(path)


def test_load_multi_identity_center_missing_region_names_entry(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "identity_centers": [
                {
                    "sso_session": "acme",
                    "sso_start_url": "https://acme.awsapps.com/start",
                    "sso_region": "us-east-1",
                }
            ]
        },
    )

    with pytest.raises(ValueError, match=r"identity_centers\[0\].*default_region"):
        load_generator_config(path)


def test_load_multi_identity_center_bad_skip_names_entry(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-east-1",
            "identity_centers": [
                {
                    "sso_session": "acme",
                    "sso_start_url": "https://acme.awsapps.com/start",
                    "sso_region": "us-east-1",
                    "skip": [["only-one"]],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match=r"identity_centers\[0\]: skip entry 0"):
        load_generator_config(path)


_VALID_CENTER = {
    "sso_session": "acme",
    "sso_start_url": "https://acme.awsapps.com/start",
    "sso_region": "us-east-1",
}


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([1, 2], r"generator config must be an object"),
        ({"identity_centers": [42]}, r"identity_centers\[0\]: .*must be an object"),
        (
            {"identity_centers": ["sso_session sso_start_url sso_region"]},
            r"identity_centers\[0\]: .*must be an object",
        ),
        (
            {"identity_centers": [{**_VALID_CENTER, "account_names": ["x"]}]},
            r"identity_centers\[0\]: account_names must be an object",
        ),
        (
            {"role_short_names": "x", "identity_centers": [_VALID_CENTER]},
            r"role_short_names must be an object",
        ),
        (
            {"identity_centers": [{**_VALID_CENTER, "skip": "nope"}]},
            r"identity_centers\[0\]: skip must be a list",
        ),
        (
            {"identity_centers": [{**_VALID_CENTER, "skip": ["ab"]}]},
            r"identity_centers\[0\]: skip entry 0",
        ),
        (
            {"identity_centers": [{**_VALID_CENTER, "profile_prefix": 3}]},
            r"identity_centers\[0\]: profile_prefix must be a string",
        ),
    ],
)
def test_load_rejects_malformed_shapes(tmp_path, data, match):
    if isinstance(data, dict):
        data = {"default_region": "us-east-1", **data}
    path = _write_json(tmp_path, data)

    with pytest.raises(ValueError, match=match):
        load_generator_config(path)


def test_load_bad_shared_skip_reported_once_without_entry_location(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-east-1",
            "skip": [["only-one"]],
            "identity_centers": [_VALID_CENTER],
        },
    )

    with pytest.raises(ValueError, match=r"^top-level skip entry 0"):
        load_generator_config(path)


def test_load_warns_about_ignored_top_level_keys(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "sso_session": "old",
            "sso_start_url": "https://old.awsapps.com/start",
            "profile_prefix": "old",
            "default_region": "us-east-1",
            "identity_centers": [_VALID_CENTER],
        },
    )

    config = load_generator_config(path)
    (center,) = config.identity_centers

    assert center.sso_session == "acme"
    assert center.profile_prefix == ""
    (warning,) = config.warnings
    assert "sso_session, sso_start_url, profile_prefix ignored" in warning


def test_load_does_not_print_warnings(tmp_path, capsys):
    path = _write_json(
        tmp_path,
        {
            "sso_session": "old",
            "default_region": "us-east-1",
            "identity_centers": [_VALID_CENTER],
        },
    )

    load_generator_config(path)

    assert capsys.readouterr().err == ""


def test_load_no_warning_without_ignored_keys(tmp_path):
    path = _write_json(
        tmp_path, {"default_region": "us-east-1", "identity_centers": [_VALID_CENTER]}
    )

    assert load_generator_config(path).warnings == ()


def test_profile_prefix_is_normalized(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-east-1",
            "identity_centers": [{**_VALID_CENTER, "profile_prefix": "Client Org"}],
        },
    )
    config = load_generator_config(path)
    roles = [AccountRole(SSOAccount("111111111111", "Prod", "a@example.com"), "Admin")]

    (entry,) = build_profile_entries({"acme": roles}, config)

    assert config.identity_centers[0].profile_prefix == "client-org"
    assert entry.profile_name == "client-org-prod"


def test_legacy_layout_profile_prefix_is_normalized(tmp_path):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-east-1", "profile_prefix": "My Org"},
    )

    (center,) = load_generator_config(path).identity_centers

    assert center.profile_prefix == "my-org"


def test_build_entries_across_identity_centers():
    acme_acct = SSOAccount("111111111111", "Prod", "a@example.com")
    beta_acct = SSOAccount("222222222222", "Prod", "b@example.com")
    config = GeneratorConfig(
        identity_centers=[
            _center(sso_session="acme", profile_prefix="acme"),
            _center(
                sso_session="beta", profile_prefix="beta", default_region="eu-west-1"
            ),
        ]
    )
    roles_by_session = {
        "acme": [
            AccountRole(acme_acct, "Admin"),
            AccountRole(acme_acct, "ReadOnly"),
        ],
        "beta": [AccountRole(beta_acct, "Admin")],
    }

    entries = build_profile_entries(roles_by_session, config)

    by_name = {e.profile_name: e for e in entries}
    assert list(by_name) == ["acme-prod-admin", "acme-prod-readonly", "beta-prod"]
    assert by_name["acme-prod-admin"].sso_session == "acme"
    assert by_name["acme-prod-admin"].region == "us-west-2"
    assert by_name["beta-prod"].sso_session == "beta"
    assert by_name["beta-prod"].region == "eu-west-1"


def test_multi_role_detection_is_per_identity_center():
    # Same account ID visible via two Identity Centers with one role each:
    # each center sees a single-role account, so no role suffix is added.
    acct = SSOAccount("111111111111", "Shared", "s@example.com")
    config = GeneratorConfig(
        identity_centers=[
            _center(sso_session="a", profile_prefix="a"),
            _center(sso_session="b", profile_prefix="b"),
        ]
    )

    entries = build_profile_entries(
        {"a": [AccountRole(acct, "Admin")], "b": [AccountRole(acct, "ReadOnly")]},
        config,
    )

    assert [e.profile_name for e in entries] == ["a-shared", "b-shared"]


def test_duplicate_profile_names_across_identity_centers_raise():
    config = GeneratorConfig(
        identity_centers=[_center(sso_session="a"), _center(sso_session="b")]
    )

    with pytest.raises(ValueError, match="Duplicate profile names generated: prod"):
        build_profile_entries(
            {
                "a": [AccountRole(SSOAccount("1", "Prod", "x"), "Admin")],
                "b": [AccountRole(SSOAccount("2", "Prod", "y"), "Admin")],
            },
            config,
        )


# --- Field validation edge cases ---


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("client-", "client"),
        ("-client-", "client"),
        ("  Client Org  ", "client-org"),
        ("", ""),
    ],
)
def test_profile_prefix_strips_edge_hyphens_and_whitespace(tmp_path, raw, expected):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-east-1", "profile_prefix": raw},
    )

    (center,) = load_generator_config(path).identity_centers

    assert center.profile_prefix == expected


def test_prefix_with_trailing_hyphen_has_single_separator(tmp_path):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-east-1", "profile_prefix": "client-"},
    )
    config = load_generator_config(path)
    roles = [AccountRole(SSOAccount("111111111111", "Prod", "a@example.com"), "Admin")]

    (entry,) = build_profile_entries({"acme": roles}, config)

    assert entry.profile_name == "client-prod"


@pytest.mark.parametrize("raw", ["  ", "-", " - ", "\t"])
def test_profile_prefix_without_content_is_rejected(tmp_path, raw):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-east-1", "profile_prefix": raw},
    )

    with pytest.raises(ValueError, match="profile_prefix must contain"):
        load_generator_config(path)


@pytest.mark.parametrize("raw", ["a[b", "a]b", "a\tb"])
def test_profile_prefix_with_unsafe_chars_is_rejected(tmp_path, raw):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-east-1", "profile_prefix": raw},
    )

    with pytest.raises(ValueError, match="profile_prefix must not contain"):
        load_generator_config(path)


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"sso_session": 5}, r"sso_session must be a string, got int"),
        ({"sso_session": ""}, r"sso_session must not be empty"),
        ({"sso_session": "my sso"}, r"sso_session must not contain whitespace"),
        ({"sso_session": "my]sso"}, r"sso_session must not contain whitespace"),
        ({"sso_start_url": None}, r"sso_start_url must be a string, got NoneType"),
        ({"sso_start_url": " "}, r"sso_start_url must not be empty"),
        ({"sso_region": ["us-east-1"]}, r"sso_region must be a string, got list"),
        ({"default_region": 1}, r"default_region must be a string, got int"),
        ({"account_names": {"1": 7}}, r"account_names\['1'\] must be a string"),
        ({"account_names": {"1": ""}}, r"account_names\['1'\] must not be empty"),
        (
            {"role_short_names": {"Admin": None}},
            r"role_short_names\['Admin'\] must be a string",
        ),
        ({"skip": [["1", 2]]}, r"skip entry 0 must be"),
        ({"skip": [["", "Admin"]]}, r"skip entry 0 must be"),
    ],
)
def test_legacy_layout_rejects_bad_field_values(tmp_path, overrides, match):
    path = _write_json(
        tmp_path, {**_VALID_CENTER, "default_region": "us-east-1", **overrides}
    )

    with pytest.raises(ValueError, match=match):
        load_generator_config(path)


def test_multi_layout_bad_field_reports_entry_location(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-east-1",
            "identity_centers": [_VALID_CENTER, {**_VALID_CENTER, "sso_session": 9}],
        },
    )

    with pytest.raises(ValueError, match=r"^identity_centers\[1\]: sso_session"):
        load_generator_config(path)


def test_bad_shared_map_value_is_rejected(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-east-1",
            "account_names": {"1": 7},
            "identity_centers": [_VALID_CENTER],
        },
    )

    with pytest.raises(
        ValueError, match=r"^top-level account_names\['1'\] must be a string"
    ):
        load_generator_config(path)


def test_null_entry_default_region_is_not_reported_as_missing(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-east-1",
            "identity_centers": [{**_VALID_CENTER, "default_region": None}],
        },
    )

    with pytest.raises(
        ValueError,
        match=r"identity_centers\[0\]: default_region must be a string, got NoneType",
    ):
        load_generator_config(path)


def test_bad_shared_default_region_is_rejected(tmp_path):
    path = _write_json(
        tmp_path, {"default_region": "", "identity_centers": [_VALID_CENTER]}
    )

    with pytest.raises(
        ValueError, match=r"^top-level default_region must not be empty"
    ):
        load_generator_config(path)


@pytest.mark.parametrize(
    ("override", "match"),
    [
        (
            {"default_region": "us-west-2\ncredential_process = /bin/echo pwned"},
            r"default_region must not contain line breaks",
        ),
        (
            {"sso_start_url": "https://acme.awsapps.com/start\r\nfoo = bar"},
            r"sso_start_url must not contain line breaks",
        ),
        (
            {"account_names": {"1": "prod]\n[profile other"}},
            r"account_names\['1'\] must not contain line breaks",
        ),
        (
            {"role_short_names": {"Admin": "admin\x00"}},
            r"role_short_names\['Admin'\] must not contain line breaks",
        ),
    ],
)
def test_values_with_line_breaks_are_rejected(tmp_path, override, match):
    path = _write_json(
        tmp_path, {**_VALID_CENTER, "default_region": "us-west-2", **override}
    )

    with pytest.raises(ValueError, match=match):
        load_generator_config(path)


@pytest.mark.parametrize(
    "region", ["x.evil.example#", "us-east-1.evil.example", "US-EAST-1", "useast1"]
)
@pytest.mark.parametrize("key", ["sso_region", "default_region"])
def test_invalid_region_names_are_rejected(tmp_path, key, region):
    path = _write_json(
        tmp_path, {**_VALID_CENTER, "default_region": "us-west-2", key: region}
    )

    with pytest.raises(ValueError, match=rf"{key} must be an AWS region name"):
        load_generator_config(path)


def test_invalid_shared_default_region_is_rejected(tmp_path):
    path = _write_json(
        tmp_path,
        {"default_region": "x.evil.example#", "identity_centers": [_VALID_CENTER]},
    )

    with pytest.raises(
        ValueError, match=r"^top-level default_region must be an AWS region name"
    ):
        load_generator_config(path)


@pytest.mark.parametrize(
    "region", ["us-east-1", "eu-central-1", "us-gov-west-1", "ap-southeast-3"]
)
def test_valid_region_names_are_accepted(tmp_path, region):
    path = _write_json(
        tmp_path, {**_VALID_CENTER, "sso_region": region, "default_region": region}
    )

    (center,) = load_generator_config(path).identity_centers
    assert center.sso_region == region
    assert center.default_region == region


def test_string_values_are_stripped(tmp_path):
    path = _write_json(
        tmp_path,
        {
            **_VALID_CENTER,
            "sso_start_url": "  https://acme.awsapps.com/start  ",
            "default_region": " us-west-2 ",
            "account_names": {"1": " prod "},
        },
    )

    (center,) = load_generator_config(path).identity_centers
    assert center.sso_start_url == "https://acme.awsapps.com/start"
    assert center.default_region == "us-west-2"
    assert center.account_names == {"1": "prod"}


@pytest.mark.parametrize(
    ("center_kwargs", "bad_name"),
    [
        ({"account_names": {"111111111111": "my prod"}}, "my prod"),
        ({"account_names": {"111111111111": "prod]"}}, "prod]"),
        ({"role_short_names": {"Admin": "ad min"}}, "acme-ad min"),
    ],
)
def test_unsafe_generated_profile_names_are_rejected(center_kwargs, bad_name):
    account = SSOAccount("111111111111", "Acme", "acme@example.com")
    roles = [AccountRole(account=account, role_name="Admin")]
    if "role_short_names" in center_kwargs:
        roles.append(AccountRole(account=account, role_name="ReadOnly"))
    config = GeneratorConfig(identity_centers=[_center(**center_kwargs)])

    with pytest.raises(
        ValueError, match=re.escape(f"Generated profile name {bad_name!r}")
    ):
        _build(roles, config)


def test_unknown_keys_warn_with_suggestion_legacy_layout(tmp_path):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-west-2", "acount_names": {}},
    )

    config = load_generator_config(path)

    assert config.warnings == (
        "unknown key 'acount_names' ignored; did you mean 'account_names'?",
    )


def test_unknown_keys_warn_at_top_level_and_in_entries(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-west-2",
            "identity_center": [],
            "identity_centers": [
                {**_VALID_CENTER, "role_shortnames": {}, "zzz": 1},
            ],
        },
    )

    config = load_generator_config(path)

    assert config.warnings == (
        "top-level unknown key 'identity_center' ignored; "
        "did you mean 'identity_centers'?",
        "identity_centers[0]: unknown key 'role_shortnames' ignored; "
        "did you mean 'role_short_names'?",
        "identity_centers[0]: unknown key 'zzz' ignored",
    )


def test_ignored_per_center_keys_are_not_also_reported_as_unknown(tmp_path):
    path = _write_json(
        tmp_path,
        {
            "default_region": "us-west-2",
            "sso_session": "x",
            "identity_centers": [_VALID_CENTER],
        },
    )

    (warning,) = load_generator_config(path).warnings
    assert "unknown" not in warning


@pytest.mark.parametrize("prefix", [".", "_", "-._-"])
def test_profile_prefix_without_letters_or_digits_is_rejected(tmp_path, prefix):
    path = _write_json(
        tmp_path,
        {**_VALID_CENTER, "default_region": "us-west-2", "profile_prefix": prefix},
    )

    with pytest.raises(ValueError, match="profile_prefix must contain at least one"):
        load_generator_config(path)


def test_region_with_trailing_newline_is_rejected_by_pattern():
    from aws_config_gen.naming import _REGION

    assert _REGION.fullmatch("us-east-1\n") is None
