"""SSO portal REST client."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Any

from aws_config_gen.errors import DiscoveryDataError
from aws_config_gen.types import SSOAccount

_BASE = "https://portal.sso.{region}.amazonaws.com/assignment"

_TIMEOUT = 10  # seconds — prevent hanging when SSO endpoint is unreachable

_PAGE_SIZE = "100"  # max_result per SSO portal page request

# Values rendered verbatim into ~/.aws/config (sso_account_id, sso_role_name)
# must not be able to start a new INI line or section.
_ACCOUNT_ID = re.compile(r"\d{12}")
_ROLE_NAME = re.compile(r"[\w+=,.@-]+")  # IAM role name charset


def _build_request(url: str, token: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"x-amz-sso_bearer_token": token})


def _malformed(endpoint: str, exc: Exception) -> DiscoveryDataError:
    msg = f"Unexpected response from {endpoint} ({type(exc).__name__}: {exc})"
    return DiscoveryDataError(msg)


def _require_match(value: object, pattern: re.Pattern[str], field: str) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        msg = f"invalid {field} {value!r}"
        raise TypeError(msg)
    return value


def _fetch_all_pages(
    endpoint: str,
    token: str,
    key: str,
    extra_params: dict[str, str] | None = None,
) -> list[Any]:
    """Fetch every page of an SSO portal listing endpoint.

    Args:
        endpoint: Fully formatted endpoint URL, without a query string.
        token: SSO bearer token.
        key: Response key holding each page's items (e.g. ``"accountList"``).
        extra_params: Additional query parameters sent with each page request.

    Returns:
        Concatenated items from ``key`` across all pages.

    Raises:
        DiscoveryDataError: If a page is not JSON or lacks ``key``.
    """
    items: list[Any] = []
    next_token: str | None = None

    while True:
        params: dict[str, str] = {**(extra_params or {}), "max_result": _PAGE_SIZE}
        if next_token is not None:
            params["next_token"] = next_token
        url = f"{endpoint}?{urllib.parse.urlencode(params)}"
        req = _build_request(url, token)
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            body = resp.read()

        try:
            data = json.loads(body)
            page = data[key]
            if not isinstance(page, list):
                msg = f"{key} must be a list, got {type(page).__name__}"
                raise TypeError(msg)
            next_token = data.get("nextToken")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            # ValueError covers json.JSONDecodeError; TypeError/AttributeError
            # cover a body that is valid JSON but not an object.
            raise _malformed(endpoint, exc) from exc

        items.extend(page)
        if not next_token:
            break

    return items


def list_accounts(token: str, region: str) -> list[SSOAccount]:
    """Fetch all SSO accounts visible to the bearer token, handling pagination.

    Args:
        token: SSO bearer token.
        region: SSO portal region.

    Returns:
        Every account visible to the token.
    """
    endpoint = f"{_BASE.format(region=region)}/accounts"
    try:
        return [
            SSOAccount(
                account_id=_require_match(acct["accountId"], _ACCOUNT_ID, "accountId"),
                account_name=acct["accountName"],
                email_address=acct["emailAddress"],
            )
            for acct in _fetch_all_pages(endpoint, token, "accountList")
        ]
    except (KeyError, TypeError) as exc:
        raise _malformed(endpoint, exc) from exc


def list_account_roles(token: str, region: str, account_id: str) -> list[str]:
    """Fetch all role names for a given account, handling pagination.

    Args:
        token: SSO bearer token.
        region: SSO portal region.
        account_id: Account whose roles to list.

    Returns:
        Every role name available in the account.
    """
    endpoint = f"{_BASE.format(region=region)}/roles"
    try:
        return [
            _require_match(role["roleName"], _ROLE_NAME, "roleName")
            for role in _fetch_all_pages(
                endpoint, token, "roleList", {"account_id": account_id}
            )
        ]
    except (KeyError, TypeError) as exc:
        raise _malformed(endpoint, exc) from exc
