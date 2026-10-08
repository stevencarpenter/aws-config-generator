"""Profile naming logic with generator config."""

from __future__ import annotations

import difflib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aws_config_gen.types import (
    AccountRole,
    GeneratorConfig,
    IdentityCenterConfig,
    ProfileEntry,
)

# Keys that only make sense per Identity Center; at the top level of a
# multi-Identity-Center config they are ignored.
_PER_CENTER_KEYS = ("sso_session", "sso_start_url", "sso_region", "profile_prefix")

# Every key an Identity Center entry (or the legacy flat layout) understands.
_CENTER_KEYS = frozenset(
    {*_PER_CENTER_KEYS, "default_region", "account_names", "role_short_names", "skip"}
)

# Keys understood at the top level of a multi-Identity-Center config. The
# per-center keys are reported separately as "ignored", not as unknown.
_MULTI_TOP_LEVEL_KEYS = _CENTER_KEYS | {"identity_centers"}


# INI section headers are ``[<type> <name>]``; whitespace, brackets or control
# characters in a session or profile name would produce a header the AWS CLI
# cannot parse, or inject extra lines into the config.
_SAFE_NAME = re.compile(r"[^\s\[\]\x00-\x1f\x7f]+")

# Any control character (notably CR/LF) in a rendered value would let it start
# a new INI line, e.g. inject ``credential_process = ...`` into a profile.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# AWS region names such as us-east-1, eu-central-1 or us-gov-west-1. The SSO
# region is interpolated into the portal hostname, so anything looser could
# send the bearer token to a different host.
_REGION = re.compile(r"[a-z]{2}(-[a-z]+)+-\d+")

_TOP_LEVEL = "top-level "


@dataclass(frozen=True)
class _SharedDefaults:
    """Validated top-level defaults inherited by every Identity Center."""

    default_region: str | None = None
    account_names: dict[str, str] = field(default_factory=dict)
    role_short_names: dict[str, str] = field(default_factory=dict)
    skip: list[tuple[str, str]] = field(default_factory=list)


def _normalize_name(name: str) -> str:
    """Lowercase and hyphenate a name for use in a profile name."""
    return name.lower().replace(" ", "-")


def _require_str(value: Any, name: str, where: str) -> str:
    """Return *value* stripped, requiring a non-empty single-line string."""
    if not isinstance(value, str):
        msg = f"{where}{name} must be a string, got {type(value).__name__}"
        raise ValueError(msg)
    value = value.strip()
    if not value:
        msg = f"{where}{name} must not be empty"
        raise ValueError(msg)
    if _CONTROL_CHARS.search(value):
        msg = f"{where}{name} must not contain line breaks or control characters, got {value!r}"
        raise ValueError(msg)
    return value


def _require_region(value: Any, name: str, where: str) -> str:
    """Ensure *value* is an AWS region name such as ``us-east-1``."""
    region = _require_str(value, name, where)
    if not _REGION.fullmatch(region):
        msg = (
            f"{where}{name} must be an AWS region name like 'us-east-1', got {region!r}"
        )
        raise ValueError(msg)
    return region


def _require_mapping(value: Any, name: str, where: str) -> dict[str, Any]:
    """Ensure *value* is a JSON object, raising ValueError otherwise."""
    if not isinstance(value, dict):
        msg = f"{where}{name} must be an object, got {type(value).__name__}"
        raise ValueError(msg)
    return value


def _require_str_mapping(value: Any, name: str, where: str) -> dict[str, str]:
    """Return a copy of a JSON object with its values validated and stripped."""
    mapping = _require_mapping(value, name, where)
    return {
        key: _require_str(item, f"{name}[{key!r}]", where)
        for key, item in mapping.items()
    }


def _parse_session_name(value: Any, where: str) -> str:
    """Validate an ``sso_session`` name so it renders a valid section header."""
    name = _require_str(value, "sso_session", where)
    if not _SAFE_NAME.fullmatch(name):
        msg = (
            f"{where}sso_session must not contain whitespace or brackets, got {name!r}"
        )
        raise ValueError(msg)
    return name


def _parse_profile_prefix(value: Any, where: str) -> str:
    """Normalize ``profile_prefix``; an absent or empty prefix means no prefix.

    Surrounding whitespace and hyphens are stripped so the prefix never yields
    names like ``client--prod``. A non-empty value that normalizes to nothing
    (e.g. ``"  "`` or ``"-"``), or has no letter or digit (e.g. ``"."``), is
    rejected rather than silently dropped or used as-is.
    """
    if not isinstance(value, str):
        msg = f"{where}profile_prefix must be a string, got {type(value).__name__}"
        raise ValueError(msg)
    if value == "":
        return ""
    prefix = _normalize_name(value.strip()).strip("-")
    if not re.search(r"[a-z0-9]", prefix):
        msg = f"{where}profile_prefix must contain at least one letter or digit, got {value!r}"
        raise ValueError(msg)
    if not _SAFE_NAME.fullmatch(prefix):
        msg = f"{where}profile_prefix must not contain whitespace or brackets, got {value!r}"
        raise ValueError(msg)
    return prefix


def _parse_skip_list(raw: Any, where: str = "") -> list[tuple[str, str]]:
    """Validate and convert skip entries to (account_id, role_name) tuples."""
    if not isinstance(raw, list):
        msg = f"{where}skip must be a list, got {type(raw).__name__}"
        raise ValueError(msg)
    result: list[tuple[str, str]] = []
    for i, pair in enumerate(raw):
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(part, str) and part for part in pair)
        ):
            msg = f"{where}skip entry {i} must be [account_id, role_name], got {pair!r}"
            raise ValueError(msg)
        result.append((pair[0], pair[1]))
    return result


def _unknown_key_warnings(
    data: dict[str, Any], known: frozenset[str], where: str
) -> list[str]:
    """Return a warning for each unrecognized key, suggesting close matches."""
    warnings: list[str] = []
    for key in sorted(k for k in data if k not in known):
        hint = difflib.get_close_matches(key, known, n=1)
        suggestion = f"; did you mean {hint[0]!r}?" if hint else ""
        warnings.append(f"{where}unknown key {key!r} ignored{suggestion}")
    return warnings


def _parse_shared_defaults(data: dict[str, Any]) -> _SharedDefaults:
    """Validate the top-level defaults of a multi-Identity-Center config once."""
    default_region = None
    if "default_region" in data:
        default_region = _require_region(
            data["default_region"], "default_region", _TOP_LEVEL
        )
    return _SharedDefaults(
        default_region=default_region,
        account_names=_require_str_mapping(
            data.get("account_names", {}), "account_names", _TOP_LEVEL
        ),
        role_short_names=_require_str_mapping(
            data.get("role_short_names", {}), "role_short_names", _TOP_LEVEL
        ),
        skip=_parse_skip_list(data.get("skip", []), _TOP_LEVEL),
    )


def _parse_identity_center(
    data: Any, shared: _SharedDefaults, where: str, warnings: list[str]
) -> IdentityCenterConfig:
    """Build one IdentityCenterConfig, layering per-entry values over *shared*.

    Per-entry maps are merged over the shared maps and the per-entry skip list
    is appended to the shared one. Unknown keys are appended to *warnings*.
    """
    data = _require_mapping(data, "identity center entry", where)
    warnings.extend(_unknown_key_warnings(data, _CENTER_KEYS, where))
    for key in ("sso_session", "sso_start_url", "sso_region"):
        if key not in data:
            msg = f"{where}missing required key {key!r}"
            raise ValueError(msg)

    # An explicit per-entry value (even null) is validated as-is rather than
    # falling back to the shared one, so typos surface instead of being masked.
    if "default_region" in data:
        default_region = _require_region(
            data["default_region"], "default_region", where
        )
    elif shared.default_region is not None:
        default_region = shared.default_region
    else:
        msg = f"{where}missing required key 'default_region'"
        raise ValueError(msg)

    return IdentityCenterConfig(
        sso_session=_parse_session_name(data["sso_session"], where),
        sso_start_url=_require_str(data["sso_start_url"], "sso_start_url", where),
        sso_region=_require_region(data["sso_region"], "sso_region", where),
        default_region=default_region,
        account_names={
            **shared.account_names,
            **_require_str_mapping(
                data.get("account_names", {}), "account_names", where
            ),
        },
        role_short_names={
            **shared.role_short_names,
            **_require_str_mapping(
                data.get("role_short_names", {}), "role_short_names", where
            ),
        },
        skip=shared.skip + _parse_skip_list(data.get("skip", []), where),
        profile_prefix=_parse_profile_prefix(data.get("profile_prefix", ""), where),
    )


def load_generator_config(path: Path) -> GeneratorConfig:
    """Read the generator config JSON and return a GeneratorConfig instance.

    Two layouts are accepted:

    * Multi Identity Center: an ``identity_centers`` list, each entry holding
      its own ``sso_session``/``sso_start_url``/``sso_region`` plus optional
      ``profile_prefix``. ``default_region``, ``account_names``,
      ``role_short_names`` and ``skip`` may be given at the top level as
      shared defaults and overridden/extended per entry.
    * Legacy single Identity Center: the same keys flat at the top level.

    Non-fatal problems are returned in ``GeneratorConfig.warnings`` for the
    caller to report; invalid configs raise ValueError.
    """
    data = _require_mapping(json.loads(path.read_text()), "generator config", "")

    warnings: list[str] = []
    if "identity_centers" not in data:
        center = _parse_identity_center(data, _SharedDefaults(), "", warnings)
        return GeneratorConfig(identity_centers=[center], warnings=tuple(warnings))

    raw_centers = data["identity_centers"]
    if not isinstance(raw_centers, list) or not raw_centers:
        msg = "identity_centers must be a non-empty list"
        raise ValueError(msg)

    warnings.extend(_unknown_key_warnings(data, _MULTI_TOP_LEVEL_KEYS, _TOP_LEVEL))
    ignored = [key for key in _PER_CENTER_KEYS if key in data]
    if ignored:
        warnings.append(
            f"top-level {', '.join(ignored)} ignored because identity_centers "
            "is set; move them into an identity_centers entry."
        )

    shared = _parse_shared_defaults(data)
    centers = [
        _parse_identity_center(raw, shared, f"identity_centers[{i}]: ", warnings)
        for i, raw in enumerate(raw_centers)
    ]

    session_counts = Counter(c.sso_session for c in centers)
    duplicates = sorted(name for name, count in session_counts.items() if count > 1)
    if duplicates:
        msg = (
            f"Duplicate sso_session names in identity_centers: {', '.join(duplicates)}"
        )
        raise ValueError(msg)

    return GeneratorConfig(identity_centers=centers, warnings=tuple(warnings))


def _validate_unique_profile_names(entries: list[ProfileEntry]) -> None:
    """Ensure generated profile names are unique."""
    duplicate_names = sorted(
        profile_name
        for profile_name, count in Counter(
            entry.profile_name for entry in entries
        ).items()
        if count > 1
    )
    if duplicate_names:
        duplicates = ", ".join(duplicate_names)
        msg = (
            f"Duplicate profile names generated: {duplicates}. "
            "Update account_names, role_short_names or profile_prefix "
            "to keep names unique."
        )
        raise ValueError(msg)


def _build_center_entries(
    roles: list[AccountRole], center: IdentityCenterConfig
) -> list[ProfileEntry]:
    """Build (unsorted, unvalidated) entries for a single Identity Center."""
    # Determine which accounts have multiple roles
    role_counts = Counter(r.account.account_id for r in roles)

    entries: list[ProfileEntry] = []
    for role in roles:
        account_id = role.account.account_id
        account_name = center.account_names.get(
            account_id,
            _normalize_name(role.account.account_name),
        )
        role_short = center.role_short_names.get(
            role.role_name,
            role.role_name.lower(),
        )

        if role_counts[account_id] > 1:
            profile_name = f"{account_name}-{role_short}"
        else:
            profile_name = account_name
        if center.profile_prefix:
            profile_name = f"{center.profile_prefix}-{profile_name}"
        if not _SAFE_NAME.fullmatch(profile_name):
            msg = (
                f"Generated profile name {profile_name!r} for account {account_id} "
                f"role {role.role_name} contains whitespace, brackets or control "
                "characters. Set a safe name via account_names or role_short_names."
            )
            raise ValueError(msg)

        entries.append(
            ProfileEntry(
                profile_name=profile_name,
                sso_session=center.sso_session,
                account_id=account_id,
                role_name=role.role_name,
                region=center.default_region,
            )
        )
    return entries


def build_profile_entries(
    roles_by_session: dict[str, list[AccountRole]],
    generator_config: GeneratorConfig,
) -> list[ProfileEntry]:
    """Build sorted ProfileEntry list across all Identity Centers.

    *roles_by_session* maps each Identity Center's ``sso_session`` name to the
    roles discovered through it. Profile names must be unique across all
    Identity Centers, since they share one ``~/.aws/config`` namespace.
    """
    entries: list[ProfileEntry] = []
    for center in generator_config.identity_centers:
        roles = roles_by_session.get(center.sso_session, [])
        entries.extend(_build_center_entries(roles, center))

    _validate_unique_profile_names(entries)
    return sorted(entries, key=lambda e: e.profile_name)
