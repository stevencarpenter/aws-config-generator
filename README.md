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
- **Zero dependencies**: Pure Python 3.14+ with no external runtime dependencies
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
--strict                   Exit 1 when discovery fails for any Identity Center (default: exit 0)
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

Treat discovery failures (expired token, network error) as errors for CI:

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

String values must not contain line breaks, and `sso_region`/`default_region` must be AWS region names
(e.g. `us-east-1`). Generated profile names must not contain whitespace or brackets; if an `account_names` or
`role_short_names` value would produce one, the run fails with an error naming the account and role.
Unrecognized keys are ignored with a warning (with a "did you mean" hint for likely typos).

### Multiple Identity Centers

To generate profiles from several Identity Centers (e.g. your own org plus a client's), list them under
`identity_centers`. Each entry becomes its own `[sso-session]` stanza in the managed block, followed by its profiles.
See [`examples/overrides.multi.example.json`](examples/overrides.multi.example.json).

```json
{
  "default_region": "us-west-2",
  "role_short_names": { "AdministratorAccess": "admin", "ReadOnlyAccess": "ro" },
  "identity_centers": [
    {
      "sso_session": "my-sso",
      "sso_start_url": "https://my-org.awsapps.com/start",
      "sso_region": "us-east-1",
      "account_names": { "111111111111": "prod" }
    },
    {
      "sso_session": "client-sso",
      "sso_start_url": "https://client-org.awsapps.com/start",
      "sso_region": "eu-west-1",
      "default_region": "eu-central-1",
      "profile_prefix": "client",
      "account_names": { "555555555555": "prod" }
    }
  ]
}
```

- Each entry requires `sso_session` (unique across entries, no whitespace or brackets), `sso_start_url` and
  `sso_region`. Apart from `profile_prefix` (where `""` means no prefix), string settings must be non-empty,
  single-line strings; surrounding whitespace is stripped. `sso_region` and `default_region` must be AWS region
  names such as `us-east-1`.
- `default_region`, `account_names`, `role_short_names` and `skip` can be set at the top level as shared defaults.
  Per-entry `default_region` replaces the shared one, per-entry maps are merged over the shared maps, and per-entry
  `skip` lists are appended to the shared list.
- `profile_prefix` (string, optional) is lowercased, spaces become hyphens, leading/trailing hyphens are stripped,
  and it is prepended with a hyphen to every profile from that Identity Center (`client-prod` above). A prefix with
  no letters or digits (e.g. `"-"`) is rejected. All profile names share one `~/.aws/config`
  namespace, so use it when two orgs would otherwise produce the same name. Duplicates are reported as an error.
- `sso_session`, `sso_start_url`, `sso_region` and `profile_prefix` at the top level are ignored (with a warning)
  when `identity_centers` is set; put them in an entry instead.
- Each session needs its own login: `aws sso login --sso-session client-sso`.
- If discovery fails for any Identity Center (expired token, network error), every failure is reported and
  `~/.aws/config` is left unchanged. This keeps a partial run from removing the profiles of the failed Identity
  Center. Exit code follows `--strict` as usual.

The single-Identity-Center layout above (keys at the top level, no `identity_centers`) is still supported, and also
accepts an optional top-level `profile_prefix`.

## How It Works

### Discovery Pipeline

1. **Load SSO Token**: For each configured Identity Center, reads the cached bearer token from `~/.aws/sso/cache/`
   for its SSO session
2. **Fetch Accounts**: Lists all accounts visible to that SSO session via the Identity Center API
3. **Fetch Roles**: For each account, lists all roles accessible to the SSO session
4. **Apply Config**: Filters out any `(account_id, role_name)` pairs in the skip list
5. **Build Profiles**: Generates profile names using configured account and role aliases; uses suffixes for multi-role
   accounts, and applies the Identity Center's `profile_prefix` if set

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

Any profiles inside the markers are replaced on the next run. Profiles outside the markers are preserved. If a
manual section outside the markers has the same name as a generated one, it is removed (a warning lists the absorbed
sections); comments directly above the following section are kept.

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
│   ├── errors.py             # Shared exceptions (DiscoveryDataError)
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
the test suite on every push and pull request against Python 3.14 and 3.14t
(free-threaded).

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
