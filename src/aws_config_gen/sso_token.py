"""SSO token cache reader."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from aws_config_gen.errors import DiscoveryDataError


class TokenExpiredError(Exception):
    """Raised when the cached SSO token has expired."""


class TokenNotFoundError(Exception):
    """Raised when no cached SSO token file is found."""


def load_sso_token(
    session_name: str,
    cache_dir: Path | None = None,
) -> str:
    """Load and validate an SSO access token from the AWS CLI cache.

    Computes the SHA-1 hash of the session name to locate the cache file,
    then validates the token has not expired.

    Raises:
        TokenNotFoundError: If the cache file does not exist.
        TokenExpiredError: If the token's expiresAt is in the past.
        DiscoveryDataError: If the cache file is not valid token JSON.
    """
    if cache_dir is None:
        cache_dir = Path.home() / ".aws" / "sso" / "cache"

    # SHA-1 matches the AWS CLI's cache key convention
    cache_key = hashlib.sha1(session_name.encode()).hexdigest()  # noqa: S324
    cache_file = cache_dir / f"{cache_key}.json"

    if not cache_file.exists():
        msg = f"No cached token for session '{session_name}': {cache_file}"
        raise TokenNotFoundError(msg)

    try:
        data = json.loads(cache_file.read_text())
        access_token = data["accessToken"]
        expires_at_str = data["expiresAt"]
        if not isinstance(access_token, str) or not isinstance(expires_at_str, str):
            msg = "accessToken and expiresAt must be strings"
            raise TypeError(msg)
        expires_at = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    except (ValueError, KeyError, TypeError) as exc:
        # ValueError covers json.JSONDecodeError and bad ISO timestamps;
        # TypeError covers a cache file that is not a JSON object.
        msg = f"Malformed token cache for session '{session_name}': {cache_file} ({type(exc).__name__}: {exc})"
        raise DiscoveryDataError(msg) from exc

    if expires_at.tzinfo is None:
        # Treat naive timestamps as UTC so the comparison below cannot raise.
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        msg = f"Token for session '{session_name}' expired at {expires_at_str}"
        raise TokenExpiredError(msg)

    return access_token
