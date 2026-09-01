"""tide.release — cut a release: from this checkout to a bottle someone else installs.

The gap this fills: developing tide locally and SHIPPING tide were two different
worlds. The shipping half lived as prose in ``packaging/tide.rb``'s header comment
("build the sdist, gh release create, set url + sha256, bump the test version") —
a five-step manual ritual nobody remembers at 1am, where one wrong sha256 silently
hands every user a formula that refuses to install.

``tide release`` is that ritual as one command:

* :mod:`tide.release.core` — the plan. Preflight (clean tree, right branch, tag
  free, ``gh`` authed) → the regression gate (reused verbatim from
  :mod:`tide.update.core`) → build the artifact → publish it → rewrite the tap
  formula. Everything is a :class:`~tide.release.core.Step`, so the DRY RUN is
  not a separate code path: it is the same plan with the mutating steps printed
  instead of run.
* :mod:`tide.release.commands` — the ``tide release`` CLI handler.

Two decisions worth knowing:

**The artifact is ``git archive`` of the tag**, not ``python -m build``. A tag's
archive is byte-deterministic, needs no build backend on the shipping machine
(``build`` is not a tide dependency and we refuse to grow one for a release path),
carries the tests — so the consumer's own gate can run — and pip installs a source
tree tarball perfectly well via the declared setuptools backend.

**The formula pins the immutable RELEASE ASSET**, never GitHub's ``/archive/``
URL: the on-the-fly archive tarball's sha changes under you, and a formula whose
sha256 no longer matches is a broken install for everyone who taps it.
"""

from __future__ import annotations

__all__ = ["core", "commands"]
