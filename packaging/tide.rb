# Homebrew formula for tide — one seat for many projects (CLI + markdown + a board).
#
# Distribution channel: Homebrew tap (installs the published release sdist — no PyPI).
# Tap this formula with:
#   brew tap tide-tools/tide https://github.com/tide-tools/homebrew-tide
#   brew install tide-tools/tide/tide
#
# The url pins the IMMUTABLE release-asset sdist uploaded to the GitHub release
# (NOT the /archive/ tarball — its sha is unstable across force-pushes).
#
# DO NOT cut a version by hand. `tide release` does all of it — preflight, the
# regression gate, `git archive` of the tag, `gh release create`, and rewriting
# the three fields below (url, sha256, and the smoke version in `test do`) from
# the sha256 of the artifact it just built. Hand-editing is how a formula ends up
# pinning a digest that does not match its url, which breaks the install for
# everyone who taps it.
#
#   tide release --dry-run     # see the whole plan + this formula as it would be
#   tide release               # cut it (asks before pushing anything)
#
# This file is the TEMPLATE of record; the live formula is Formula/tide.rb in the
# tap at tide-tools/homebrew-tide. Source repo lives at tide-tools/tide.

class Tide < Formula
  include Language::Python::Virtualenv

  desc "One seat for many projects: CLI + markdown + a board on localhost"
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
