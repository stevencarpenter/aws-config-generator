# aws_config_gen

Auto-generate AWS SSO profiles from AWS Identity Center for `~/.aws/config`.

## Overview

`aws_config_gen` automatically discovers all AWS accounts and roles available through your AWS Identity Center (SSO)
session and generates human-friendly AWS CLI profiles. It reads a checked-in generator config, merges profiles into your
existing `~/.aws/config` without disrupting manual entries, and supports filtering accounts/roles.

### Key Features

- **Automatic discovery**: Enumerates all accounts and roles from Identity Center in a single pass
- **Human-friendly naming**: Shortens account names and role names using a checked-in generator config
- **Smart profile naming**: Single-role accounts get simple names; multi-role accounts add role suffixes (e.g., `prod`, `prod-admin`)
- **Non-destructive merge**: Preserves manually edited profiles using marker-based insertion
- **Zero dependencies**: Pure Python 3.10+ with no external runtime dependencies
- **Token-aware**: Checks SSO token validity and provides helpful error messages
- **Dry-run mode**: Preview generated profiles before writing

## Installation

Install from source via `uv`:

```bash
uv pip install --project .
```

Or install with dev dependencies for testing/linting:

```bash
uv pip install --project .[dev]
```

## Usage

### Basic Usage

Discover all roles and generate profiles in dry-run mode:

```bash
aws-config-gen --dry-run
```

Write generated profiles to `~/.aws/config`:

```bash
aws-config-gen
```

### Command-line Options

```
--generator-config PATH    Path to overrides.json (default: ~/.config/aws-config-gen/overrides.json)
--config PATH              Path to AWS config file (default: ~/.aws/config)
--dry-run                  Print generated config to stdout; don't write
--strict                   Exit 1 on token failures (default: exit 0)
```

### Examples

Specify a custom generator config file:

```bash
aws-config-gen --generator-config ~/my-config.json
```

Write to a test config file:

```bash
aws-config-gen --config /tmp/test-aws-config
```

Treat token failures as errors for CI:

```bash
aws-config-gen --strict
```

### Using generated profiles

Once profiles are written to `~/.aws/config`, use them like any other AWS CLI
profile:

```bash
# One-time authentication for the SSO session
aws sso login --sso-session my-sso

# Use a generated profile
aws sts get-caller-identity --profile prod-admin

# Or export it for the current shell
export AWS_PROFILE=prod-admin
```

## Configuration

### Generator Config File (`overrides.json`)

The generator config file controls naming, session parameters, and skip rules. Default location:
`~/.config/aws-config-gen/overrides.json`.

A ready-to-copy sample lives in [`examples/overrides.example.json`](examples/overrides.example.json), and
[`examples/aws-config.example`](examples/aws-config.example) shows the resulting `~/.aws/config` — a single
SSO session spanning many accounts, with one-to-many assumable roles per account.

**Schema:**

```json
{
  "sso_session": "my-sso",
  "sso_start_url": "https://mycompany.awsapps.com/start",
  "sso_region": "us-east-1",
  "default_region": "us-west-2",
  "account_names": {
    "123456789012": "prod",
    "123456789013": "staging",
    "123456789014": "dev"
  },
  "role_short_names": {
    "ReadOnlyPlus": "ro",
    "DeveloperAccess": "dev",
    "PowerUserAccess": "power"
  },
  "skip": [
    ["123456789012", "UnusedRole"],
    ["123456789014", "RestrictedRole"]
  ]
}
```

**Parameters:**

- `sso_session` (string): SSO session name from `~/.aws/config` or `~/.aws/sso/cache/`
- `sso_start_url` (string): AWS SSO start URL for your organization
- `sso_region` (string): AWS region hosting Identity Center (usually `us-east-1`)
- `default_region` (string): Default AWS region for generated profiles
- `account_names` (object): Map account IDs to human-friendly names (optional)
- `role_short_names` (object): Map role names to shorter display names (optional)
- `skip` (array): List of `[account_id, role_name]` pairs to exclude (optional)

## How It Works

### Discovery Pipeline

1. **Load SSO Token**: Reads cached bearer token from `~/.aws/sso/cache/` for the specified SSO session
2. **Fetch Accounts**: Lists all accounts visible to your SSO session via the Identity Center API
3. **Fetch Roles**: For each account, lists all roles accessible to the SSO session
4. **Apply Config**: Filters out any `(account_id, role_name)` pairs in the skip list
5. **Build Profiles**: Generates profile names using configured account and role aliases; uses suffixes for multi-role
   accounts

### Profile Naming Logic

Given a set of account-role combinations:

- If an account has **one role**: profile name = account name (e.g., `prod`)
- If an account has **multiple roles**: profile name = account name + role short name (e.g., `prod-admin`, `prod-developer`)

Names are lowercased and spaces are converted to hyphens. Custom mappings in `overrides.json` override defaults.

### Config File Merge

Generated profiles are inserted between markers in `~/.aws/config`:

```ini
# ... manual profiles above ...

# BEGIN aws_config_gen managed block — do not edit
[sso-session my-sso]
sso_start_url = https://mycompany.awsapps.com/start
sso_region = us-east-1
sso_registration_scopes = sso:account:access

[profile generated-profile-1]
sso_session = my-sso
sso_account_id = 123456789012
sso_role_name = ReadOnlyPlus
region = us-west-2

[profile generated-profile-2]
sso_session = my-sso
sso_account_id = 123456789013
sso_role_name = PowerUserAccess
region = us-west-2
# END aws_config_gen managed block

# ... manual profiles below ...
```

Any profiles inside the markers are replaced on the next run. Profiles outside the markers are preserved.

## Development

### Running Tests

Run all tests:

```bash
uv run --group dev pytest --cov=aws_config_gen --cov-report=term-missing
```

Run a specific test file:

```bash
uv run --group dev pytest tests/test_naming.py -v
```

### Linting and Formatting

Check code with Ruff:

```bash
uv run --group dev ruff check src tests
```

Format code:

```bash
uv run --group dev ruff format src tests
```

### Project Structure

```
aws_config_gen/
├── src/aws_config_gen/
│   ├── __main__.py           # Entry point
│   ├── cli.py                # Command-line parser and main logic
│   ├── discovery.py          # Account and role discovery orchestration
│   ├── naming.py             # Profile naming and override loading
│   ├── sso_client.py         # AWS Identity Center REST client
│   ├── sso_token.py          # SSO token cache reader
│   ├── config_writer.py      # AWS config file rendering and merge
│   └── types.py              # Data types (SSOAccount, AccountRole, etc.)
├── tests/
│   ├── conftest.py           # Pytest fixtures
│   ├── test_cli.py
│   ├── test_config_writer.py
│   ├── test_integration.py
│   ├── test_naming.py
│   ├── test_sso_client.py
│   └── test_sso_token.py
└── pyproject.toml            # Package metadata and dependencies
```

### Key Modules

- **`cli.py`**: Parses arguments, orchestrates discovery → naming → rendering, and writes output
- **`discovery.py`**: Loads SSO token and enumerates all accessible accounts/roles
- **`naming.py`**: Builds profile entries from roles, applying generator config naming rules
- **`sso_client.py`**: HTTP client for AWS Identity Center API (accounts, roles)
- **`sso_token.py`**: Reads cached SSO bearer token from filesystem
- **`config_writer.py`**: Renders profiles to INI format and merges into config file using markers
- **`types.py`**: Dataclass definitions for type safety

## Troubleshooting

### Token Not Found

```
Run `aws sso login --sso-session my-sso` to authenticate.
```

The SSO token cache doesn't exist. Run the indicated command to create it.

### Token Expired

```
Run `aws sso login --sso-session my-sso` to refresh.
```

The cached token has expired. Run the indicated command to refresh it.

### Wrong SSO Session

Verify the `sso_session` in `overrides.json` matches the session name in your `~/.aws/config`:

```bash
grep "sso_session\|sso_start_url" ~/.aws/config
```

### No Profiles Generated

Check that your generator config file is valid:

```bash
python -m json.tool ~/.config/aws-config-gen/overrides.json
```

Verify your SSO session is authenticated:

```bash
aws sso login --sso-session my-sso
```

## Releasing

Versions are derived automatically from git tags via
[`hatch-vcs`](https://github.com/ophidian-project/hatch-vcs) — there is no
version string to bump in `pyproject.toml`. To cut a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```

Pushing a `v*.*.*` tag triggers `.github/workflows/release.yml`, which runs the
test suite, builds the sdist + wheel with `uv build`, and publishes a GitHub
Release with auto-generated notes and the built artifacts attached.

Continuous integration (`.github/workflows/ci.yml`) runs lint, format check, and
the test suite on every push and pull request against Python 3.10 through 3.14,
including 3.14t (free-threaded).

Users install a released version with:

```bash
uv tool install git+https://github.com/stevencarpenter/aws-config-generator
```

## Automating Updates

To keep profiles current, run the generator on a schedule or after
authenticating. For example, refresh profiles whenever you log in:

```bash
aws sso login --sso-session my-sso && aws-config-gen
```

You can also wire this into a shell startup hook, a cron job, or a dotfile
manager's post-apply step so profiles regenerate automatically.

## License

Released under the [MIT License](LICENSE).
