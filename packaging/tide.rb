# Homebrew formula for tide — simplified, synchronous orchestration machine.
#
# Distribution channel: Homebrew tap (installs the published release sdist — no PyPI).
# Tap this formula with:
#   brew tap tide-tools/tide https://github.com/tide-tools/homebrew-tide
#   brew install tide-tools/tide/tide
#
# The url pins the IMMUTABLE release-asset sdist uploaded to the GitHub release
# (NOT the /archive/ tarball — its sha is unstable across force-pushes). To cut a
# new version: build the sdist, `gh release create vX.Y.Z dist/tide-X.Y.Z.tar.gz`,
# then set url to that asset + sha256 = `shasum -a 256` of the UPLOADED asset, and
# bump the test version below. Source repo lives at tide-tools/tide.

class Tide < Formula
  include Language::Python::Virtualenv

  desc "Simplified, synchronous, human-driven orchestration machine (pure CLI + markdown)"
  homepage "https://github.com/tide-tools/tide"

  url "https://github.com/tide-tools/tide/releases/download/v1.0.2/tide-1.0.2.tar.gz"
  sha256 "PLACEHOLDER_SHA256_FILL_AFTER_TAG"  # filled from the uploaded v1.0.2 sdist asset at publish
  license "MIT"

  depends_on "python@3.12"

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "tide 1.0.2", shell_output("#{bin}/tide version")
    system bin/"tide", "help"
  end
end
