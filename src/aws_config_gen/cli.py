"""Command-line interface for aws_config_gen."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import urllib.error
from pathlib import Path
from collections.abc import Sequence

from aws_config_gen.config_writer import render_profiles, write_config
from aws_config_gen.discovery import discover_all_roles
from aws_config_gen.errors import DiscoveryDataError
from aws_config_gen.naming import build_profile_entries, load_generator_config
from aws_config_gen.sso_token import TokenExpiredError, TokenNotFoundError
from aws_config_gen.types import AccountRole

# Everything discovery can reasonably raise for one Identity Center: token
# problems, network failures (URLError and raw socket errors such as
# TimeoutError are OSError subclasses), truncated HTTP responses, and malformed
# token cache / portal API data. Anything else is a bug and should surface.
_DISCOVERY_ERRORS = (
    TokenExpiredError,
    TokenNotFoundError,
    DiscoveryDataError,
    OSError,
    http.client.HTTPException,
)


def _print_discovery_error(session: str, exc: Exception) -> None:
    """Print a user-facing message for a failed Identity Center discovery."""
    login = f"aws sso login --sso-session {session}"
    if isinstance(exc, TokenExpiredError):
        msg = f"Run `{login}` to refresh."
    elif isinstance(exc, TokenNotFoundError):
        msg = f"Run `{login}` to authenticate."
    elif isinstance(exc, urllib.error.HTTPError):
        # HTTPError subclasses URLError, so it must be checked first
        if exc.code == 401:
            msg = f"SSO token rejected (HTTP 401). Run `{login}` to re-authenticate."
        else:
            msg = (
                f"AWS Identity Center API error for sso-session {session} "
                f"(HTTP {exc.code}): {exc.reason}"
            )
    elif isinstance(exc, urllib.error.URLError):
        msg = (
            f"Failed to reach AWS Identity Center for sso-session {session}: "
            f"{exc.reason}"
        )
    elif isinstance(exc, (OSError, http.client.HTTPException)):
        msg = (
            f"Failed to reach AWS Identity Center for sso-session {session}: "
            f"{type(exc).__name__}: {exc}"
        )
    else:
        # DiscoveryDataError: corrupt token cache or unexpected API response.
        # Logging in again rewrites the token cache.
        msg = (
            f"Unexpected data while discovering sso-session {session} "
            f"({type(exc).__name__}: {exc}). If this persists, run `{login}`."
        )
    print(msg, file=sys.stderr)


def _print_invalid_generator_config(path: Path, exc: Exception) -> None:
    print(
        f"Invalid generator config file {path}: {exc}",
        file=sys.stderr,
    )


def _print_write_config_error(path: Path, exc: Exception) -> None:
    print(
        f"Failed to write AWS config {path}: {exc}",
        file=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aws-config-gen",
        description="Auto-generate AWS SSO profiles from Identity Center.",
    )
    parser.add_argument(
        "--generator-config",
        type=Path,
        default=None,
        help="Path to overrides.json (default: ~/.config/aws-config-gen/overrides.json).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to AWS config file (default: ~/.aws/config).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated config to stdout instead of writing.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Exit 1 when discovery fails for any Identity Center, e.g. an "
            "expired token or network error (default: exit 0)."
        ),
    )
    return parser


def cli(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    generator_config_path: Path = (
        args.generator_config.expanduser()
        if args.generator_config
        else Path.home() / ".config" / "aws-config-gen" / "overrides.json"
    )
    try:
        generator_config = load_generator_config(generator_config_path)
    except FileNotFoundError:
        print(
            f"Generator config file not found: {generator_config_path}",
            file=sys.stderr,
        )
        return 1
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        _print_invalid_generator_config(generator_config_path, exc)
        return 1

    for warning in generator_config.warnings:
        print(f"[aws-config-gen] Warning: {warning}", file=sys.stderr)

    config_path: Path = (
        args.config.expanduser() if args.config else Path.home() / ".aws" / "config"
    )

    roles_by_session: dict[str, list[AccountRole]] = {}
    failed_sessions: list[str] = []
    for center in generator_config.identity_centers:
        try:
            roles_by_session[center.sso_session] = discover_all_roles(
                center.sso_session,
                center.sso_region,
                center.skip,
            )
        except _DISCOVERY_ERRORS as exc:
            _print_discovery_error(center.sso_session, exc)
            failed_sessions.append(center.sso_session)

    if failed_sessions:
        # Writing a partial managed block would silently delete the profiles
        # of every Identity Center that failed, so leave the config untouched.
        print(
            "Discovery failed for sso-session(s): "
            f"{', '.join(failed_sessions)}; AWS config left unchanged.",
            file=sys.stderr,
        )
        return 1 if args.strict else 0

    try:
        entries = build_profile_entries(roles_by_session, generator_config)
    except ValueError as exc:
        _print_invalid_generator_config(generator_config_path, exc)
        return 1

    generated_block = render_profiles(entries, generator_config)

    if args.dry_run:
        print(generated_block, end="")
        return 0

    try:
        write_config(config_path, generated_block)
    except (ValueError, OSError) as exc:
        # OSError: unreadable config, read-only directory, disk full, ...
        _print_write_config_error(config_path, exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
