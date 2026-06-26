"""tide.verify — an isolated verification affordance for built artifacts (fix F7).

The dogfood runs all shipped a working HTML app but left the "no console errors
on load" criterion *unproven* — there was no cheap, collision-free way to actually
serve the thing and look at it. This module is that affordance:

1. **Stage** the artifact (an HTML file, or a directory of one) into a fresh
   per-run temp dir, so the check never touches or mutates the source tree.
2. **Serve** it on an **ephemeral** port — we bind to port ``0`` and let the OS
   assign a free one (``server_address[1]``), so two ``tide verify`` runs never
   collide on a fixed port.
3. **Check** it: an HTTP ``GET`` of the entry must return ``200``; and — only if
   ``node`` is on PATH — a lightweight syntax smoke of the page's inline
   ``<script>`` blocks.

Everything is stdlib-only (``http.server`` + ``urllib``); ``node`` is optional and
its absence is reported, never fatal.

Documented recipe — node smoke of an HTML page
----------------------------------------------
``node`` cannot ``--check`` a ``.html`` file (it parses it as JS and chokes on
``<``). So we **extract each inline ``<script>``** body, write it to a temp
``.js``, and run ``node --check <file>`` (a parse-only syntax check, no execution).
External (``<script src=…>``) and non-JS (``type="application/json"`` …) blocks are
skipped. This catches the most common "blank page" cause — a syntax error in the
inline app script — without a real browser. To do it by hand:

    # pull the inline script out of index.html and syntax-check it
    python3 -c "import sys,tide.verify as v; \
        print('\\n'.join(v.extract_inline_scripts(open(sys.argv[1]).read())))" \
        index.html > app.js
    node --check app.js

``register`` wires the thin ``tide verify <path> [--no-node]`` handler.
"""

from __future__ import annotations

import contextlib
import functools
import http.server
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Tuple

from .arc.stream import StreamError

HTTP_OK = 200
DEFAULT_TIMEOUT = 5.0
_HOST = "127.0.0.1"
_INDEX_NAMES = ("index.html", "index.htm")

# A <script …>BODY</script> block. DOTALL so multi-line bodies are captured; the
# attrs group lets us skip external (src=) and non-JS (type=) blocks.
_SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.IGNORECASE | re.DOTALL)
_TYPE_RE = re.compile(r"""type\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)


class VerifyError(StreamError):
    """A verify-time failure (bad/missing artifact, no servable entry).

    Subclasses :class:`tide.arc.stream.StreamError` so ``cli.main`` catches it on
    the same arm (prints ``tide: …``, exits nonzero).
    """


# --- free port -------------------------------------------------------------

def free_port() -> int:
    """Ask the OS for an unused TCP port (bind to ``0``, read it back, release).

    Exposed for the documented recipe/tests. The live server in :func:`serve`
    binds to ``0`` *directly* (no separate pick → no TOCTOU race); this helper is
    for callers that need a port number up front.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((_HOST, 0))
        return s.getsockname()[1]


# --- serving ---------------------------------------------------------------

class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler that doesn't spam the orchestrator's stderr."""

    def log_message(self, *_args) -> None:  # noqa: D401 - silence access log
        pass


@contextlib.contextmanager
def serve(directory: Path, *, host: str = _HOST) -> Iterator[int]:
    """Serve *directory* on an OS-assigned ephemeral port; yield that port.

    Binds to port ``0`` so the kernel hands back a free port (read via
    ``server_address[1]``) — two concurrent verifies never collide. The server
    runs on a daemon thread and is fully torn down on exit.
    """
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer((host, 0), handler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def http_status(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> Tuple[int, bytes]:
    """GET *url*, returning ``(status_code, body)``; HTTP errors yield their code."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""


# --- artifact staging ------------------------------------------------------

def find_entry(directory: Path) -> str:
    """Pick the entry file to request inside *directory*.

    Prefers ``index.html``/``index.htm``; else the first ``*.html`` (sorted, so the
    pick is deterministic). Raises :class:`VerifyError` when none exists.
    """
    directory = Path(directory)
    for name in _INDEX_NAMES:
        if (directory / name).is_file():
            return name
    htmls = sorted(p.name for p in directory.glob("*.html"))
    if htmls:
        return htmls[0]
    raise VerifyError(
        "verify: no .html entry found in {0}".format(directory)
    )


def stage_artifact(path: Path, dest: Path) -> str:
    """Copy *path* (an HTML file or a directory) into *dest*; return the entry name.

    A file is copied in under its own name; a directory has its contents copied in
    (so the served root mirrors the artifact). Never mutates the source.
    """
    path = Path(path)
    dest = Path(dest)
    if path.is_dir():
        for item in path.iterdir():
            target = dest / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        return find_entry(dest)
    if path.is_file():
        shutil.copy2(path, dest / path.name)
        if path.suffix.lower() not in (".html", ".htm"):
            raise VerifyError(
                "verify: artifact {0} is not an .html file".format(path.name)
            )
        return path.name
    raise VerifyError("verify: no such artifact: {0}".format(path))


# --- node smoke ------------------------------------------------------------

def node_available() -> bool:
    """True when a ``node`` binary is on PATH (the node smoke is optional)."""
    return shutil.which("node") is not None


def extract_inline_scripts(html: str) -> List[str]:
    """Return the bodies of inline JS ``<script>`` blocks in *html*, in order.

    Skips external (``src=``) blocks and non-JS ``type=`` blocks (e.g.
    ``application/json``, ``text/template``); ``module`` and any ``*javascript*``
    type are kept. Empty/whitespace bodies are dropped.
    """
    out: List[str] = []
    for attrs, body in _SCRIPT_RE.findall(html):
        if "src=" in attrs.lower():
            continue
        m = _TYPE_RE.search(attrs)
        if m:
            t = m.group(1).lower()
            if "javascript" not in t and t != "module":
                continue
        if body.strip():
            out.append(body)
    return out


def node_check(script: str) -> Tuple[bool, str]:
    """Syntax-check one JS *script* body via ``node --check``; ``(ok, detail)``.

    Writes the body to a temp ``.js`` (``node --check`` needs a file, and cannot
    read ``.html``) and parses it. ``node --check`` never executes the code.
    """
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as f:
        f.write(script)
        tmp = Path(f.name)
    try:
        proc = subprocess.run(
            ["node", "--check", str(tmp)],
            capture_output=True,
            text=True,
        )
    finally:
        tmp.unlink(missing_ok=True)
    ok = proc.returncode == 0
    detail = (proc.stderr or proc.stdout or "").strip()
    return ok, detail


# --- result ----------------------------------------------------------------

@dataclass
class VerifyResult:
    """Outcome of a verify run; ``ok`` is the overall pass/fail."""

    entry: str
    port: int
    status: int
    http_ok: bool
    node_ran: bool = False
    node_ok: bool = True
    scripts_checked: int = 0
    messages: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Overall pass: HTTP 200 and (if it ran) the node smoke clean."""
        return self.http_ok and (not self.node_ran or self.node_ok)


# --- orchestration ---------------------------------------------------------

def verify(path: Path, *, node: bool = True) -> VerifyResult:
    """Stage → serve (ephemeral port) → check *path*; return a :class:`VerifyResult`.

    Pure of CLI concerns so it stays unit-testable. ``node=False`` skips the node
    smoke even when ``node`` is installed.
    """
    src = Path(path)
    if not src.exists():
        raise VerifyError("verify: no such artifact: {0}".format(src))

    with tempfile.TemporaryDirectory(prefix="tide-verify-") as td:
        dest = Path(td)
        entry = stage_artifact(src, dest)

        with serve(dest) as port:
            url = "http://{0}:{1}/{2}".format(_HOST, port, entry)
            status, _ = http_status(url)
        http_ok = status == HTTP_OK

        result = VerifyResult(
            entry=entry, port=port, status=status, http_ok=http_ok
        )
        result.messages.append(
            "http {0} {1} ({2})".format(
                status, "OK" if http_ok else "FAIL", url
            )
        )

        _run_node_smoke(dest / entry, entry, result, node)

    return result


def _run_node_smoke(
    entry_path: Path, entry: str, result: VerifyResult, node: bool
) -> None:
    """Best-effort node syntax smoke of *entry_path*'s inline scripts (mutates result)."""
    if not node:
        result.messages.append("node: skipped (--no-node)")
        return
    if not entry.lower().endswith((".html", ".htm")):
        return
    if not node_available():
        result.messages.append("node: not found — inline-script smoke skipped")
        return

    scripts = extract_inline_scripts(entry_path.read_text(encoding="utf-8"))
    if not scripts:
        result.node_ran = True
        result.messages.append("node: no inline scripts to check")
        return

    result.node_ran = True
    failures: List[str] = []
    for i, script in enumerate(scripts, 1):
        ok, detail = node_check(script)
        result.scripts_checked += 1
        if not ok:
            failures.append("script #{0}: {1}".format(i, detail))
    if failures:
        result.node_ok = False
        result.messages.append(
            "node: {0}/{1} inline script(s) FAILED syntax".format(
                len(failures), len(scripts)
            )
        )
        result.messages.extend("  " + f for f in failures)
    else:
        result.messages.append(
            "node: {0} inline script(s) OK".format(len(scripts))
        )


# --- portability invariant (tool ⊥ instance) ------------------------------
#
# The keystone for *sharing* tide: the shipped TOOL must be cleanly separable
# from THIS instance, so a second person can `pip install tide` / `tide init`
# without inheriting our content, paths, or PII. This checker is the enforcement
# gate behind that bright line (see CLAUDE.md "tool ⊥ instance").
#
# SCOPE — what it scans, and why. Distribution is the **package** (wheel/sdist),
# whose contents are `src/tide/` only (verified: `uv build` ships nothing but the
# `tide/` package + metadata; the dev `.tide/`, `examples/`, etc. are git-tracked
# instance history that does NOT travel with `pip install`). So the check targets
# exactly the two surfaces a new user actually receives:
#   1. the shipped package source (`src/tide/**/*.py`) + `pyproject.toml` metadata
#   2. a fresh `tide init` skeleton (booted into a throwaway tmpdir)
# It deliberately does NOT walk the whole git tree — flagging dogfood under
# `.tide/`/`examples/` would be noise, since none of it ships.

# An absolute home-dir root baked into source is the canonical "this instance
# leaked into the tool" smell. `~/…` (the portable form) is fine; `/Users/<me>/`
# and `/home/<me>/` are not.
_ABS_HOME_RE = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+")


@dataclass
class PortableLeak:
    """One portability violation: an absolute home path or an instance token."""

    source: str   # "<file>" or "tide-init:<relpath>"
    line: int
    kind: str     # "abs-home-path" | "instance-token"
    detail: str   # the matched text / token
    snippet: str  # the offending line (trimmed)


@dataclass
class PortableReport:
    """Outcome of a ``tide verify --portable`` run; ``ok`` is the pass/fail."""

    leaks: List[PortableLeak] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.leaks


def default_instance_tokens() -> List[str]:
    """Instance-specific literals to forbid in shipped source, auto-detected.

    The most portable way to catch "this machine leaked in" without hardcoding a
    name is to forbid the *current* user's home path + username — if those appear
    in shipped source it is, by definition, a leak. Callers extend this with
    ``--instance-token`` (e.g. known instance project-names).
    """
    home = Path.home()
    tokens = {str(home), home.name}
    tokens.discard("")
    tokens.discard("/")
    return sorted(t for t in tokens if t)


def scan_text(text: str, source: str, tokens: List[str]) -> List[PortableLeak]:
    """Scan *text* line-by-line for absolute home paths + any *tokens*."""
    leaks: List[PortableLeak] = []
    for i, raw in enumerate(text.splitlines(), 1):
        for m in _ABS_HOME_RE.finditer(raw):
            leaks.append(
                PortableLeak(source, i, "abs-home-path", m.group(0), raw.strip()[:120])
            )
        for tok in tokens:
            if tok and tok in raw:
                leaks.append(
                    PortableLeak(source, i, "instance-token", tok, raw.strip()[:120])
                )
    return leaks


def package_source_dir() -> Path:
    """The shipped package's source dir (this module's package = ``src/tide``)."""
    return Path(__file__).resolve().parent


def _is_text_file(path: Path) -> bool:
    """Heuristic: a file is text if its leading bytes hold no NUL (skip binaries)."""
    try:
        return b"\x00" not in path.read_bytes()[:8192]
    except OSError:
        return False


def scan_package_source(pkg_dir: Path, tokens: List[str]) -> List[PortableLeak]:
    """Scan every TEXT file under *pkg_dir* (the shipped package) for leaks.

    Scans ALL text files, not just ``*.py`` — a ``.md``/``.json``/``.toml`` added
    under ``src/tide/`` would ship in the wheel and must be covered too. Skips
    ``__pycache__`` (compiled bytecode embeds compile-time abs paths and never
    ships in a wheel) and any binary (NUL-containing) file.
    """
    pkg_dir = Path(pkg_dir)
    leaks: List[PortableLeak] = []
    for f in sorted(pkg_dir.rglob("*")):
        if not f.is_file() or "__pycache__" in f.parts:
            continue
        if not _is_text_file(f):
            continue
        rel = f.relative_to(pkg_dir.parent) if pkg_dir.parent in f.parents else f
        leaks.extend(
            scan_text(f.read_text(encoding="utf-8", errors="replace"), str(rel), tokens)
        )
    return leaks


def _pyproject_path() -> Optional[Path]:
    """Best-effort locate the dev-tree ``pyproject.toml`` (src/tide → src → repo)."""
    candidate = package_source_dir().parent.parent / "pyproject.toml"
    return candidate if candidate.is_file() else None


def scan_init_skeleton(tokens: List[str]) -> List[PortableLeak]:
    """Boot a fresh ``tide init`` (+arc +contract) into a tmpdir; scan all output.

    Exercises the real creation paths an actual new user hits — including the
    contract passport (the site of the former abs-path-bake bug). Asserts NO file
    the tool *produces* carries an absolute home path or instance token.

    CRITICAL — catches the actual leak vector. The bug baked the *init root's own*
    absolute path into a generated file. On macOS the tmpdir resolves under
    ``/private/var/folders/…``, which the ``/(Users|home)/`` regex never matches —
    so the regex ALONE false-passes here. We therefore add the init root's absolute
    path (raw and ``/private``-resolved forms) as instance tokens for THIS scan; a
    re-baked abs path is then flagged platform-agnostically.
    """
    from . import init_home
    from .arc import stream
    from .contract import lifecycle

    leaks: List[PortableLeak] = []
    with tempfile.TemporaryDirectory(prefix="tide-portable-") as td:
        home = Path(td) / "control-home"
        home.mkdir()

        # The leak vector: any generated file echoing the init root's abs path.
        root_tokens = {str(Path(td)), str(Path(td).resolve()), str(home), str(home.resolve())}
        skeleton_tokens = sorted(set(tokens) | root_tokens)

        init_home.unfold_control_home(home, name="demo")
        stream.new_arc(home, "probe-arc")
        lifecycle.new(home, "probe-arc", goal="ship it", criteria="done when green")

        for f in sorted(home.rglob("*")):
            if not f.is_file() or "__pycache__" in f.parts:
                continue
            rel = "tide-init:{0}".format(f.relative_to(home))
            leaks.extend(
                scan_text(f.read_text(encoding="utf-8", errors="replace"), rel, skeleton_tokens)
            )
    return leaks


def check_portable(
    *,
    instance_tokens: Optional[List[str]] = None,
    pkg_dir: Optional[Path] = None,
    include_auto_tokens: bool = True,
) -> PortableReport:
    """Run the full portability invariant; return a :class:`PortableReport`.

    Scans (1) the shipped package source, (2) ``pyproject.toml`` metadata, and
    (3) a fresh ``tide init`` skeleton, for absolute home paths + instance tokens.
    """
    tokens: List[str] = list(instance_tokens or [])
    if include_auto_tokens:
        tokens.extend(default_instance_tokens())
    tokens = sorted(set(t for t in tokens if t))

    pkg = Path(pkg_dir) if pkg_dir else package_source_dir()
    report = PortableReport()

    pkg_leaks = scan_package_source(pkg, tokens)
    report.leaks.extend(pkg_leaks)
    report.messages.append(
        "package source ({0}): {1}".format(pkg, _verdict(pkg_leaks))
    )

    pyproject = _pyproject_path()
    if pyproject is not None:
        meta_leaks = scan_text(
            pyproject.read_text(encoding="utf-8"), "pyproject.toml", tokens
        )
        report.leaks.extend(meta_leaks)
        report.messages.append("pyproject.toml: {0}".format(_verdict(meta_leaks)))

    init_leaks = scan_init_skeleton(tokens)
    report.leaks.extend(init_leaks)
    report.messages.append("tide init skeleton: {0}".format(_verdict(init_leaks)))

    for lk in report.leaks:
        report.messages.append(
            "  LEAK {0}:{1} [{2}] {3} — {4}".format(
                lk.source, lk.line, lk.kind, lk.detail, lk.snippet
            )
        )
    return report


def _verdict(leaks: List[PortableLeak]) -> str:
    return "clean" if not leaks else "{0} LEAK(S)".format(len(leaks))


# --- CLI wiring ------------------------------------------------------------

def _cmd_verify(args) -> int:
    if getattr(args, "portable", False):
        return _cmd_verify_portable(args)
    if not args.path:
        raise VerifyError("verify: a PATH is required (or pass --portable)")
    result = verify(args.path, node=not args.no_node)
    verdict = "PASS" if result.ok else "FAIL"
    print("tide verify: {0}  ({1})".format(verdict, args.path))
    for line in result.messages:
        print("  " + line)
    return 0 if result.ok else 1


def _cmd_verify_portable(args) -> int:
    report = check_portable(instance_tokens=list(args.instance_token or []))
    verdict = "PASS" if report.ok else "FAIL"
    print("tide verify --portable: {0}".format(verdict))
    for line in report.messages:
        print("  " + line)
    return 0 if report.ok else 1


def register(subparsers) -> None:
    """Add the top-level ``verify`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "verify",
        help="serve a built artifact (HTTP 200 + node smoke), or --portable: tool ⊥ instance",
    )
    p.add_argument(
        "path",
        nargs="?",
        help="path to an HTML file or a directory of one (omit with --portable)",
    )
    p.add_argument(
        "--no-node",
        action="store_true",
        help="skip the optional node inline-script syntax smoke",
    )
    p.add_argument(
        "--portable",
        action="store_true",
        help="check tool ⊥ instance: no abs home paths / instance tokens in the "
        "shipped package or a fresh `tide init` (fails loud on a leak)",
    )
    p.add_argument(
        "--instance-token",
        action="append",
        metavar="TOKEN",
        help="extra literal to forbid in shipped source (repeatable; e.g. an "
        "instance project name)",
    )
    p.set_defaults(func=_cmd_verify, _cmd="verify")
