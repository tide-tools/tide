# Homebrew formula for tide — simplified, synchronous orchestration machine.
#
# Distribution channel: Homebrew tap (installs the GitHub source tarball — no PyPI).
# Tap this formula with:
#   brew tap socaseinpoint/tide https://github.com/socaseinpoint/homebrew-tide
#   brew install socaseinpoint/tide/tide
#
# The url is the GitHub release tarball for the tagged version; sha256 is the
# digest of that tarball. To cut a new version: tag vX.Y.Z, push, then update
# url + sha256 (shasum -a 256 of the archive tarball) and the test version below.

class Tide < Formula
  include Language::Python::Virtualenv

  desc "Simplified, synchronous, human-driven orchestration machine (pure CLI + markdown)"
  homepage "https://github.com/socaseinpoint/tide"

  url "https://github.com/socaseinpoint/tide/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256_FILL_AFTER_TAG"  # filled from the v0.1.0 tarball at publish
  license "MIT"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "tide 0.1.0", shell_output("#{bin}/tide version")
    system bin/"tide", "help"
  end
end
