"""INI config rendering and marker-based merge."""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

from aws_config_gen.types import GeneratorConfig, ProfileEntry

BEGIN_MARKER = "# BEGIN aws_config_gen managed block — do not edit"
END_MARKER = "# END aws_config_gen managed block"
SECTION_PATTERN = re.compile(r"^\[(?P<section>[^\]]+)\]\s*$")


def render_profiles(
    entries: list[ProfileEntry], generator_config: GeneratorConfig
) -> str:
    """Render the full managed block including markers and all stanzas.

    Each Identity Center gets its own ``[sso-session]`` stanza followed by the
    profiles that use it, in the order the Identity Centers are configured.

    Raises:
        ValueError: If an entry references an ``sso_session`` that is not one
            of the configured Identity Centers (it would otherwise be dropped).
    """
    known_sessions = {c.sso_session for c in generator_config.identity_centers}
    unknown = sorted({e.sso_session for e in entries} - known_sessions)
    if unknown:
        msg = f"Profile entries reference unknown sso_session(s): {', '.join(unknown)}"
        raise ValueError(msg)

    lines: list[str] = [BEGIN_MARKER]

    for i, center in enumerate(generator_config.identity_centers):
        if i > 0:
            lines.append("")

        # sso-session stanza
        lines.append(f"[sso-session {center.sso_session}]")
        lines.append(f"sso_start_url = {center.sso_start_url}")
        lines.append(f"sso_region = {center.sso_region}")
        lines.append("sso_registration_scopes = sso:account:access")

        # profile stanzas for this session
        for entry in entries:
            if entry.sso_session != center.sso_session:
                continue
            lines.append("")
            lines.append(f"[profile {entry.profile_name}]")
            lines.append(f"sso_session = {entry.sso_session}")
            lines.append(f"sso_account_id = {entry.account_id}")
            lines.append(f"sso_role_name = {entry.role_name}")
            lines.append(f"region = {entry.region}")

    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def _extract_section_names(content: str) -> set[str]:
    """Return INI section names from the provided content."""
    section_names: set[str] = set()
    for line in content.splitlines():
        match = SECTION_PATTERN.match(line.strip())
        if match:
            section_names.add(match.group("section"))
    return section_names


def _remove_sections(content: str, sections_to_remove: set[str]) -> str:
    """Remove INI sections (header + body) whose name is in the set.

    Comment and blank lines at the end of a removed section are held back: if
    a section header follows, they most likely describe that next section and
    are kept; if a key line follows, they were part of the removed body. At
    end of input they are kept, so no user comment is lost by guessing.
    """
    out: list[str] = []
    skipping = False
    pending: list[str] = []  # comment/blank lines seen while skipping
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        match = SECTION_PATTERN.match(stripped)
        if match:
            out.extend(pending)
            pending = []
            skipping = match.group("section") in sections_to_remove
        elif skipping:
            if not stripped or stripped.startswith(("#", ";")):
                pending.append(line)
            else:
                pending = []  # a key line: preceding comments were body
            continue
        if not skipping:
            out.append(line)
    out.extend(pending)
    # Collapse runs of blank lines left behind
    result = re.sub(r"\n{3,}", "\n\n", "".join(out))
    return result


def merge_config(existing_content: str, generated_block: str) -> str:
    """Merge a generated block into existing config content.

    If BEGIN/END markers are found, replace that range (inclusive).
    Otherwise, prepend the generated block before existing content.
    Duplicate manual sections that collide with generated ones are absorbed.
    """
    existing_lines = existing_content.splitlines(keepends=True)

    begin_idx: int | None = None
    end_idx: int | None = None

    for i, line in enumerate(existing_lines):
        stripped = line.rstrip("\n\r")
        if stripped == BEGIN_MARKER:
            if begin_idx is not None:
                raise ValueError(
                    "Multiple managed block BEGIN markers found in config."
                )
            begin_idx = i
        elif stripped == END_MARKER:
            if begin_idx is None:
                raise ValueError(
                    "Managed block END marker found before BEGIN marker in config."
                )
            if end_idx is not None:
                raise ValueError("Multiple managed block END markers found in config.")
            end_idx = i

    if begin_idx is not None:
        if end_idx is None:
            raise ValueError(
                "Managed block BEGIN marker found without matching END marker."
            )
        before = "".join(existing_lines[:begin_idx])
        after = "".join(existing_lines[end_idx + 1 :])
        unmanaged = before + after
    else:
        before = ""
        after = existing_content
        unmanaged = existing_content

    # Remove manual sections that collide with generated ones
    generated_sections = _extract_section_names(generated_block)
    duplicate_sections = _extract_section_names(unmanaged) & generated_sections
    if duplicate_sections:
        names = ", ".join(sorted(duplicate_sections))
        print(
            f"[aws-config-gen] Absorbed manual sections now managed: {names}",
            file=sys.stderr,
        )
        before = _remove_sections(before, duplicate_sections)
        after = _remove_sections(after, duplicate_sections)

    if begin_idx is not None:
        # Replace managed block in place, preserving surrounding content
        return before + generated_block + after
    # No markers — prepend generated block
    remaining = after.strip()
    if remaining:
        return generated_block + "\n" + remaining + "\n"
    return generated_block


def write_config(config_path: Path, generated_block: str) -> None:
    """Atomically write the merged config to disk.

    If the config file already exists, its mode is preserved. Otherwise, the
    file is created with mode 0o600 (owner read/write only). AWS config
    contains SSO session URLs, account IDs, and role names that should not
    be world-readable.
    """
    existing_content = ""
    existing_mode: int | None = None
    if config_path.exists():
        existing_content = config_path.read_text()
        existing_mode = config_path.stat().st_mode & 0o777

    merged = merge_config(existing_content, generated_block)

    config_path.parent.mkdir(parents=True, exist_ok=True)
    target_mode = existing_mode if existing_mode is not None else 0o600
    # mkstemp creates the file with mode 0o600, so the contents are never
    # readable by other users, even briefly. The file lives next to the
    # target so os.replace stays an atomic same-filesystem rename.
    fd, tmp_name = tempfile.mkstemp(
        dir=config_path.parent, prefix=f".{config_path.name}.", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as tmp_file:
            tmp_file.write(merged)
        os.chmod(tmp_path, target_mode)
        os.replace(tmp_path, config_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
