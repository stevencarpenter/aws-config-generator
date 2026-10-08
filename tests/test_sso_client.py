"""Tests for SSO portal REST client."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from aws_config_gen.errors import DiscoveryDataError
from aws_config_gen.sso_client import list_account_roles, list_accounts
from aws_config_gen.types import SSOAccount

TOKEN = "fake-bearer-token"
REGION = "us-west-2"


def _mock_response(body: dict) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


class TestListAccounts:
    def test_single_page(self):
        payload = {
            "accountList": [
                {
                    "accountId": "111111111111",
                    "accountName": "Acme",
                    "emailAddress": "acme@example.com",
                },
                {
                    "accountId": "222222222222",
                    "accountName": "Beta",
                    "emailAddress": "beta@example.com",
                },
            ],
            "nextToken": None,
        }

        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response(payload)
            result = list_accounts(TOKEN, REGION)

        assert result == [
            SSOAccount("111111111111", "Acme", "acme@example.com"),
            SSOAccount("222222222222", "Beta", "beta@example.com"),
        ]
        assert mock_urlopen.call_count == 1
        req = mock_urlopen.call_args[0][0]
        assert "max_result=100" in req.full_url
        assert req.get_header("X-amz-sso_bearer_token") == TOKEN

    def test_pagination(self):
        page1 = {
            "accountList": [
                {
                    "accountId": "111111111111",
                    "accountName": "Acme",
                    "emailAddress": "acme@example.com",
                },
            ],
            "nextToken": "page2-token",
        }
        page2 = {
            "accountList": [
                {
                    "accountId": "222222222222",
                    "accountName": "Beta",
                    "emailAddress": "beta@example.com",
                },
            ],
            "nextToken": None,
        }

        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _mock_response(page1),
                _mock_response(page2),
            ]
            result = list_accounts(TOKEN, REGION)

        assert len(result) == 2
        assert result[0].account_id == "111111111111"
        assert result[1].account_id == "222222222222"
        assert mock_urlopen.call_count == 2

        second_req = mock_urlopen.call_args_list[1][0][0]
        assert "next_token=page2-token" in second_req.full_url

    def test_empty_account_list(self):
        payload = {"accountList": [], "nextToken": None}

        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response(payload)
            result = list_accounts(TOKEN, REGION)

        assert result == []

    def test_http_error(self):
        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://example.com",
                code=401,
                msg="Unauthorized",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                list_accounts(TOKEN, REGION)

            assert exc_info.value.code == 401


class TestListAccountRoles:
    def test_single_page(self):
        payload = {
            "roleList": [
                {"roleName": "AdminAccess", "accountId": "111111111111"},
                {"roleName": "ReadOnly", "accountId": "111111111111"},
            ],
            "nextToken": None,
        }

        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.return_value = _mock_response(payload)
            result = list_account_roles(TOKEN, REGION, "111111111111")

        assert result == ["AdminAccess", "ReadOnly"]
        req = mock_urlopen.call_args[0][0]
        assert "account_id=111111111111" in req.full_url

    def test_pagination(self):
        page1 = {
            "roleList": [{"roleName": "Admin", "accountId": "111111111111"}],
            "nextToken": "roles-page2",
        }
        page2 = {
            "roleList": [{"roleName": "ReadOnly", "accountId": "111111111111"}],
            "nextToken": None,
        }

        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                _mock_response(page1),
                _mock_response(page2),
            ]
            result = list_account_roles(TOKEN, REGION, "111111111111")

        assert result == ["Admin", "ReadOnly"]
        assert mock_urlopen.call_count == 2

        second_req = mock_urlopen.call_args_list[1][0][0]
        assert "next_token=roles-page2" in second_req.full_url

    def test_http_error(self):
        with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="https://example.com",
                code=403,
                msg="Forbidden",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )
            with pytest.raises(urllib.error.HTTPError) as exc_info:
                list_account_roles(TOKEN, REGION, "111111111111")

            assert exc_info.value.code == 403


def _mock_raw_response(body: bytes) -> MagicMock:
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


@pytest.mark.parametrize(
    "body",
    [
        b"<html>not json</html>",
        b"[]",
        json.dumps({"nextToken": None}).encode(),
        json.dumps({"accountList": {"not": "a list"}}).encode(),
        json.dumps({"accountList": [{"accountName": "no id"}]}).encode(),
        json.dumps({"accountList": ["not-an-object"]}).encode(),
    ],
)
def test_list_accounts_malformed_response_raises(body):
    with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _mock_raw_response(body)
        with pytest.raises(DiscoveryDataError, match="Unexpected response"):
            list_accounts(TOKEN, REGION)


@pytest.mark.parametrize(
    "body",
    [
        b"{truncated",
        json.dumps({"roleList": [{"accountId": "1"}]}).encode(),
    ],
)
def test_list_account_roles_malformed_response_raises(body):
    with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _mock_raw_response(body)
        with pytest.raises(DiscoveryDataError, match="Unexpected response"):
            list_account_roles(TOKEN, REGION, "111111111111")


@pytest.mark.parametrize(
    "account",
    [
        {"accountId": "1111\n[profile x]", "accountName": "a", "emailAddress": "e"},
        {"accountId": 111111111111, "accountName": "a", "emailAddress": "e"},
    ],
)
def test_list_accounts_rejects_unsafe_account_id(account):
    with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _mock_response({"accountList": [account]})
        with pytest.raises(DiscoveryDataError, match="invalid accountId"):
            list_accounts(TOKEN, REGION)


@pytest.mark.parametrize("role_name", ["Admin\ncredential_process = x", "a b", ""])
def test_list_account_roles_rejects_unsafe_role_name(role_name):
    with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _mock_response(
            {"roleList": [{"roleName": role_name}]}
        )
        with pytest.raises(DiscoveryDataError, match="invalid roleName"):
            list_account_roles(TOKEN, REGION, "111111111111")


def test_list_account_roles_accepts_iam_role_charset():
    name = "AWSReservedSSO_Admin+x=y,z.w@corp-1"
    with patch("aws_config_gen.sso_client.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = _mock_response({"roleList": [{"roleName": name}]})
        assert list_account_roles(TOKEN, REGION, "111111111111") == [name]
