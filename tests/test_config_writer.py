"""Tests for INI config rendering and marker-based merge."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pathlib import Path

from aws_config_gen.config_writer import (
    BEGIN_MARKER,
    END_MARKER,
    _remove_sections,
    merge_config,
    render_profiles,
    write_config,
)
from aws_config_gen.types import GeneratorConfig, IdentityCenterConfig, ProfileEntry


def _make_center(**kwargs: object) -> IdentityCenterConfig:
    defaults: dict[str, object] = {
        "sso_session": "test-session",
        "sso_start_url": "https://test.awsapps.com/start/#",
        "sso_region": "us-west-2",
        "default_region": "us-west-2",
        "account_names": {},
        "role_short_names": {},
        "skip": [],
    }
    defaults.update(kwargs)
    return IdentityCenterConfig(**defaults)  # type: ignore[arg-type]


def _make_generator_config(**kwargs: object) -> GeneratorConfig:
    return GeneratorConfig(identity_centers=[_make_center(**kwargs)])


def _make_entry(**kwargs: str) -> ProfileEntry:
    defaults = {
        "profile_name": "acme-ro",
        "sso_session": "test-session",
        "account_id": "111111111111",
        "role_name": "ReadOnlyPlus",
        "region": "us-west-2",
    }
    defaults.update(kwargs)
    return ProfileEntry(**defaults)


def test_render_profiles_produces_correct_format():
    generator_config = _make_generator_config()
    entries = [
        _make_entry(profile_name="acme-ro", role_name="ReadOnlyPlus"),
        _make_entry(profile_name="acme-admin", role_name="AdministratorAccess"),
    ]

    result = render_profiles(entries, generator_config)

    assert result.startswith(BEGIN_MARKER + "\n")
    assert result.endswith(END_MARKER + "\n")

    # sso-session stanza
    assert "[sso-session test-session]" in result
    assert "sso_start_url = https://test.awsapps.com/start/#" in result
    assert "sso_region = us-west-2" in result
    assert "sso_registration_scopes = sso:account:access" in result

    # profile stanzas
    assert "[profile acme-ro]" in result
    assert "[profile acme-admin]" in result
    assert "sso_session = test-session" in result
    assert "sso_account_id = 111111111111" in result
    assert "sso_role_name = ReadOnlyPlus" in result
    assert "sso_role_name = AdministratorAccess" in result
    assert "region = us-west-2" in result


def test_render_profiles_stanzas_separated_by_blank_lines():
    generator_config = _make_generator_config()
    entries = [_make_entry(), _make_entry(profile_name="acme-admin")]

    result = render_profiles(entries, generator_config)

    # Between sso-session stanza and first profile, and between profiles
    assert "\n\n[profile " in result


def test_render_profiles_empty_entries():
    generator_config = _make_generator_config()

    result = render_profiles([], generator_config)

    assert "[sso-session test-session]" in result
    assert "[profile " not in result
    assert result.startswith(BEGIN_MARKER + "\n")
    assert result.endswith(END_MARKER + "\n")


def test_render_profiles_multiple_identity_centers_grouped_by_session():
    generator_config = GeneratorConfig(
        identity_centers=[
            _make_center(sso_session="acme", sso_start_url="https://acme/start"),
            _make_center(
                sso_session="beta",
                sso_start_url="https://beta/start",
                sso_region="eu-west-1",
            ),
        ]
    )
    # Sorted by profile name, interleaving sessions
    entries = [
        _make_entry(profile_name="a-beta", sso_session="beta"),
        _make_entry(profile_name="b-acme", sso_session="acme"),
    ]

    result = render_profiles(entries, generator_config)

    acme_idx = result.index("[sso-session acme]")
    beta_idx = result.index("[sso-session beta]")
    assert acme_idx < result.index("[profile b-acme]") < beta_idx
    assert beta_idx < result.index("[profile a-beta]")
    assert "sso_start_url = https://beta/start\nsso_region = eu-west-1" in result
    # Stanzas are blank-line separated, no double blanks
    assert "\n\n[sso-session beta]" in result
    assert "\n\n\n" not in result
    assert result.count(BEGIN_MARKER) == 1
    assert result.count(END_MARKER) == 1


def test_merge_config_replaces_existing_managed_block():
    existing = (
        "[profile manual-profile]\nregion = eu-west-1\n\n"
        f"{BEGIN_MARKER}\nold managed content\n{END_MARKER}\n\n"
        "[profile another-manual]\nregion = us-east-1\n"
    )
    new_block = f"{BEGIN_MARKER}\nnew managed content\n{END_MARKER}\n"

    result = merge_config(existing, new_block)

    assert "new managed content" in result
    assert "old managed content" not in result
    assert "[profile manual-profile]" in result
    assert "[profile another-manual]" in result


def test_merge_config_prepends_when_no_markers():
    existing = "[profile manual-profile]\nregion = eu-west-1\n"
    new_block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    result = merge_config(existing, new_block)

    assert result.startswith(new_block)
    assert "[profile manual-profile]" in result
    assert result.index(BEGIN_MARKER) < result.index("[profile manual-profile]")


def test_merge_config_prepends_to_empty_content():
    new_block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    result = merge_config("", new_block)

    assert result == new_block


def test_merge_config_preserves_content_outside_markers():
    before = "[profile before]\nregion = us-west-2\n\n"
    after = "\n[profile after]\nregion = eu-west-1\n"
    existing = f"{before}{BEGIN_MARKER}\nold stuff\n{END_MARKER}\n{after}"
    new_block = f"{BEGIN_MARKER}\nnew stuff\n{END_MARKER}\n"

    result = merge_config(existing, new_block)

    assert result.startswith("[profile before]")
    assert "[profile after]" in result
    assert "new stuff" in result
    assert "old stuff" not in result


def test_merge_config_absorbs_duplicate_section_outside_managed_block(capsys):
    existing = (
        "[sso-session test-session]\n"
        "sso_start_url = https://old.example.com\n"
        "sso_region = us-east-1\n\n"
        "[profile prod]\n"
        "region = us-east-1\n\n"
        "[profile keep-me]\n"
        "region = eu-west-1\n"
    )
    new_block = (
        f"{BEGIN_MARKER}\n"
        "[sso-session test-session]\n"
        "sso_start_url = https://example.com/start\n"
        "sso_region = us-west-2\n"
        "sso_registration_scopes = sso:account:access\n\n"
        "[profile prod]\n"
        "sso_session = test-session\n"
        "sso_account_id = 111111111111\n"
        "sso_role_name = ReadOnly\n"
        "region = us-west-2\n"
        f"{END_MARKER}\n"
    )

    result = merge_config(existing, new_block)

    # Manual duplicates removed, non-conflicting manual section kept
    assert "[profile keep-me]" in result
    assert result.count("[profile prod]") == 1
    assert result.count("[sso-session test-session]") == 1
    # Generated block is present
    assert BEGIN_MARKER in result
    assert "sso_start_url = https://example.com/start" in result
    # Stderr warning
    captured = capsys.readouterr()
    assert "Absorbed" in captured.err


def test_write_config_creates_file(tmp_path: Path):
    config_path = tmp_path / "config"
    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    write_config(config_path, block)

    assert config_path.exists()
    assert config_path.read_text() == block


def test_write_config_merges_into_existing(tmp_path: Path):
    config_path = tmp_path / "config"
    config_path.write_text("[profile manual]\nregion = us-east-1\n")

    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    write_config(config_path, block)

    content = config_path.read_text()
    assert "managed content" in content
    assert "[profile manual]" in content


def test_write_config_atomic_no_tmp_leftover(tmp_path: Path):
    config_path = tmp_path / "config"
    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    write_config(config_path, block)

    assert [p.name for p in tmp_path.iterdir()] == ["config"]
    assert config_path.exists()
    assert config_path.read_text() == block


def test_write_config_replaces_managed_block_preserves_manual(tmp_path: Path):
    config_path = tmp_path / "config"
    original = (
        "[profile manual]\nregion = us-east-1\n\n"
        f"{BEGIN_MARKER}\nold content\n{END_MARKER}\n"
    )
    config_path.write_text(original)

    new_block = f"{BEGIN_MARKER}\nnew content\n{END_MARKER}\n"
    write_config(config_path, new_block)

    content = config_path.read_text()
    assert "new content" in content
    assert "old content" not in content
    assert "[profile manual]" in content


def test_write_config_preserves_existing_mode(tmp_path: Path):
    config_path = tmp_path / "config"
    config_path.write_text("[profile manual]\nregion = us-east-1\n")
    os.chmod(config_path, 0o600)

    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"
    write_config(config_path, block)

    assert config_path.stat().st_mode & 0o777 == 0o600


def test_write_config_new_file_is_owner_only(tmp_path: Path):
    """New AWS config files must default to 0o600 — not world-readable.

    AWS config contains SSO start URLs, account IDs, and role names that
    should not be exposed to other local users.
    """
    config_path = tmp_path / "config"
    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    write_config(config_path, block)

    assert config_path.stat().st_mode & 0o777 == 0o600


def test_write_config_preserves_more_restrictive_mode(tmp_path: Path):
    """If user has set the file to 0o400 (read-only), preserve it."""
    config_path = tmp_path / "config"
    config_path.write_text("[profile manual]\nregion = us-east-1\n")
    os.chmod(config_path, 0o400)

    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"
    write_config(config_path, block)

    assert config_path.stat().st_mode & 0o777 == 0o400


# --- Marker corruption tests ---


class TestMergeConfigCorruptedMarkers:
    def test_duplicate_begin_markers(self):
        content = f"{BEGIN_MARKER}\nstuff\n{BEGIN_MARKER}\nmore\n{END_MARKER}\n"
        with pytest.raises(ValueError, match="Multiple managed block BEGIN"):
            merge_config(content, "new block\n")

    def test_end_before_begin(self):
        content = f"{END_MARKER}\nstuff\n"
        with pytest.raises(ValueError, match="END marker found before BEGIN"):
            merge_config(content, "new block\n")

    def test_duplicate_end_markers(self):
        content = f"{BEGIN_MARKER}\nstuff\n{END_MARKER}\nmore\n{END_MARKER}\n"
        with pytest.raises(ValueError, match="Multiple managed block END"):
            merge_config(content, "new block\n")

    def test_begin_without_end(self):
        content = f"{BEGIN_MARKER}\nstuff\nno end marker\n"
        with pytest.raises(ValueError, match="BEGIN marker found without matching END"):
            merge_config(content, "new block\n")


def test_render_profiles_rejects_entries_for_unknown_sessions():
    generator_config = _make_generator_config()
    entries = [_make_entry(sso_session="not-configured")]

    with pytest.raises(ValueError, match="unknown sso_session.*not-configured"):
        render_profiles(entries, generator_config)


def test_write_config_temp_file_is_never_world_readable(tmp_path: Path):
    """The temp file must be 0o600 from creation, before any chmod."""
    config_path = tmp_path / "config"
    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"
    seen_modes: list[int] = []
    real_replace = os.replace

    def spy_replace(src, dst):
        seen_modes.append(os.stat(src).st_mode & 0o777)
        real_replace(src, dst)

    old_umask = os.umask(0o022)  # would yield 0o644 for a plain open()
    try:
        with (
            patch("aws_config_gen.config_writer.os.chmod"),
            patch("aws_config_gen.config_writer.os.replace", side_effect=spy_replace),
        ):
            write_config(config_path, block)
    finally:
        os.umask(old_umask)

    assert seen_modes == [0o600]


def test_write_config_cleans_up_temp_file_on_failure(tmp_path: Path):
    config_path = tmp_path / "config"
    config_path.write_text("[profile manual]\n")
    block = f"{BEGIN_MARKER}\nmanaged content\n{END_MARKER}\n"

    with (
        patch("aws_config_gen.config_writer.os.replace", side_effect=OSError("boom")),
        pytest.raises(OSError, match="boom"),
    ):
        write_config(config_path, block)

    assert [p.name for p in tmp_path.iterdir()] == ["config"]
    assert config_path.read_text() == "[profile manual]\n"


def test_remove_sections_keeps_comment_describing_next_section():
    content = (
        "[profile prod]\n"
        "region = us-east-1\n"
        "\n"
        "# this comment documents the keep profile below\n"
        "[profile keep]\n"
        "region = eu-west-1\n"
    )

    result = _remove_sections(content, {"profile prod"})

    assert result == (
        "\n# this comment documents the keep profile below\n"
        "[profile keep]\n"
        "region = eu-west-1\n"
    )


def test_remove_sections_drops_comments_inside_removed_body():
    content = (
        "[profile prod]\n"
        "# old region, kept for reference\n"
        "region = us-east-1\n"
        "[profile keep]\n"
        "region = eu-west-1\n"
    )

    result = _remove_sections(content, {"profile prod"})

    assert result == "[profile keep]\nregion = eu-west-1\n"


def test_remove_sections_keeps_trailing_comments_at_end_of_input():
    content = "[profile keep]\nregion = eu-west-1\n[profile prod]\nregion = x\n; end\n"

    result = _remove_sections(content, {"profile prod"})

    assert result == "[profile keep]\nregion = eu-west-1\n; end\n"
