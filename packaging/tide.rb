# Homebrew formula for tide — simplified, synchronous orchestration machine.
#
# Distribution channel: Homebrew tap.
# Tap this formula with:
#   brew tap tide-project/tide https://github.com/tide-project/tide
#   brew install tide-project/tide/tide
#
# TODO(publish): Before a real release —
#   1. Publish the wheel to PyPI: python -m build && twine upload dist/*
#   2. Replace the url below with the real PyPI sdist URL (e.g.
#      https://files.pythonhosted.org/packages/.../tide-0.1.0.tar.gz).
#   3. Compute sha256: shasum -a 256 dist/tide-0.1.0.tar.gz
#   4. Replace the sha256 PLACEHOLDER below with the real digest.
#   Publishing and token rotation are human-gated — do NOT automate step 1.

class Tide < Formula
  include Language::Python::Virtualenv

  desc "Simplified, synchronous, human-driven orchestration machine (pure CLI + markdown)"
  homepage "https://github.com/tide-project/tide"

  # TODO(publish): fill url and sha256 from the released PyPI sdist tarball.
  url "https://files.pythonhosted.org/packages/source/t/tide/tide-0.1.0.tar.gz"
  sha256 "PLACEHOLDER_SHA256_FILL_FROM_RELEASED_TARBALL"  # TODO(publish): replace before release
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
