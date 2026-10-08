"""Shared test fixtures for aws_config_gen."""

from __future__ import annotations

import pytest

from aws_config_gen.types import GeneratorConfig, IdentityCenterConfig, SSOAccount


@pytest.fixture
def sample_identity_center() -> IdentityCenterConfig:
    return IdentityCenterConfig(
        sso_session="test-session",
        sso_start_url="https://test.awsapps.com/start/#",
        sso_region="us-west-2",
        default_region="us-west-2",
        account_names={
            "111111111111": "acme",
            "222222222222": "acme-dev",
        },
        role_short_names={
            "ReadOnlyPlus": "ro",
            "AdministratorAccess": "admin",
        },
        skip=[],
    )


@pytest.fixture
def sample_generator_config(
    sample_identity_center: IdentityCenterConfig,
) -> GeneratorConfig:
    return GeneratorConfig(identity_centers=[sample_identity_center])


@pytest.fixture
def sample_accounts() -> list[SSOAccount]:
    return [
        SSOAccount(
            account_id="111111111111",
            account_name="Acme Corp",
            email_address="acme@example.com",
        ),
        SSOAccount(
            account_id="222222222222",
            account_name="Acme Dev",
            email_address="acme-dev@example.com",
        ),
        SSOAccount(
            account_id="333333333333",
            account_name="Other Account",
            email_address="other@example.com",
        ),
    ]
