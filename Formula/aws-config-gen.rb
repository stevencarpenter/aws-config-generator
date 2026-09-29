# Homebrew formula for aws-config-gen.
#
# This repository doubles as its own Homebrew tap, so the formula lives here
# rather than in a separate homebrew-* repo. See the README for install
# instructions.
#
# On release: bump `url`/`sha256` to the new tag's sdist. The sdist (not the
# git tag archive) is used deliberately — hatch-vcs derives the version from
# git metadata, which a tag archive lacks, whereas the sdist carries the
# version in PKG-INFO.
class AwsConfigGen < Formula
  include Language::Python::Virtualenv

  desc "Auto-generate AWS SSO profiles from AWS Identity Center"
  homepage "https://github.com/stevencarpenter/aws-config-generator"
  url "https://github.com/stevencarpenter/aws-config-generator/releases/download/v0.1.0/aws_config_gen-0.1.0.tar.gz"
  sha256 "622fe70988af1c74d0072a4bb56b8163cf582a0937b0f795f2f7f703a8d224ca"
  license "MIT"
  head "https://github.com/stevencarpenter/aws-config-generator.git", branch: "main"

  depends_on "python@3.14"

  # aws-config-gen has zero runtime dependencies, so no `resource` blocks are
  # needed — the virtualenv contains just this package.
  def install
    virtualenv_install_with_resources
  end

  test do
    # --help exercises argument parsing without touching the network or
    # ~/.aws, and confirms the console script is linked correctly.
    assert_match "Auto-generate AWS SSO profiles", shell_output("#{bin}/aws-config-gen --help")

    # A missing generator config must fail cleanly with exit status 1 rather
    # than tracebacking.
    output = shell_output("#{bin}/aws-config-gen --generator-config #{testpath}/absent.json 2>&1", 1)
    assert_match "Generator config file not found", output

    # End-to-end dry run: a valid generator config plus an expired token
    # cache should report the login hint and exit 0 (non-strict default).
    (testpath/"overrides.json").write <<~JSON
      {
        "sso_session": "brew-test",
        "sso_start_url": "https://example.awsapps.com/start",
        "sso_region": "us-east-1",
        "default_region": "us-west-2"
      }
    JSON
    output = shell_output(
      "#{bin}/aws-config-gen --generator-config #{testpath}/overrides.json --dry-run 2>&1",
    )
    assert_match "aws sso login --sso-session brew-test", output
  end
end
