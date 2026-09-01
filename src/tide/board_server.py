"""tide.board_server — ``tide board``: the board in the box, served from the package.

The living board (tide-stack/board: serve_live.py + live_projection.py, ~12k
lines) is an app of its own and does NOT ride in this package — it is pinned to
one machine today (cand 187) and carries surfaces a fresh install doesn't have.
What the box needs on day one is smaller and honest: **open a browser and see
your control-home** — the home stream, every rostered project's stream, and the
work-cards when the ``work`` plugin is on. That projection the engine already
computes (:mod:`tide.arc.board`); this module only puts it behind a localhost
port.

Design constraints, from the release thread's decisions:

* **localhost only by default** (decision 13) — the phone path is
  ``tailscale serve``, an instruction, not a listener; ``--bind`` exists for
  containers, not for the open LAN.
* **the port is a flag, not a constant in someone's plist** (work 51) —
  ``--port`` with a default.
* stdlib only — the package promises no web deps, and keeps that promise.

Every GET re-renders from disk, so the page is never stale by more than a
refresh (the page also asks the browser to reload itself every 30s). The seam to
the living board: when tide-stack/board is unpinned from its home paths, ``tide
board`` is the verb that should learn to launch it — same name, same flag, a
richer page.
"""

from __future__ import annotations

import datetime as _dt
import html as _html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, List, Optional

from . import (__version__, fields, paths, plugins, quickstart,
               roster as roster_mod)
from .arc.stream import StreamError

DEFAULT_PORT = 8765
DEFAULT_BIND = "127.0.0.1"

_REFRESH_SECONDS = 30

_PAGE_TOP = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="{refresh}">
<title>tide — {title}</title>
<style>
  body {{ background:#101418; color:#d7dde3; font:14px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;
         margin:0; padding:1.2rem 1.6rem 3rem; }}
  h1 {{ font-size:1rem; color:#8fb8e8; letter-spacing:.06em; margin:0 0 .2rem; }}
  h2 {{ font-size:.85rem; color:#e8c98f; letter-spacing:.05em; margin:1.6rem 0 .3rem;
       border-bottom:1px solid #2a3138; padding-bottom:.2rem; }}
  pre {{ white-space:pre-wrap; margin:.3rem 0 0; overflow-x:auto; }}
  .meta {{ color:#5c6873; font-size:.78rem; margin:0 0 .8rem; }}
  .path {{ color:#5c6873; font-weight:normal; font-size:.75rem; }}
  .works li {{ list-style:none; margin:.15rem 0; }}
  .works {{ margin:.4rem 0 0; padding:0; }}
  .st {{ color:#101418; border-radius:3px; padding:0 .35em; font-size:.75rem; margin-right:.5em; }}
  .st-open {{ background:#8fb8e8; }} .st-taken {{ background:#e8c98f; }}
  .st-review {{ background:#c9a2e8; }} .st-done {{ background:#93d3a2; }}
  .empty {{ color:#5c6873; }}
</style>
"""


class BoardServeError(StreamError):
    """A user-facing ``tide board`` error (no control-home, port busy)."""


# --- projection ------------------------------------------------------------

def _esc(text: str) -> str:
    return _html.escape(text or "", quote=False)


def _stream_html(root: Path) -> str:
    """The project's STREAM board as escaped ``<pre>`` text (closed rows hidden).

    Closed entries are finished history — on the wall they are noise, exactly as
    the SessionStart hook decided; ``tide status`` in a terminal keeps them.
    """
    from .arc import board as _board  # lazy: keeps module import light

    try:
        text = _board.render_board(Path(root), include_closed=False)
    except Exception as exc:  # noqa: BLE001 — one broken project must not kill the wall
        text = "(board unavailable: {0})".format(exc)
    return "<pre>{0}</pre>".format(_esc(text))


def _work_rows(root: Path) -> List[Dict[str, str]]:
    """The project's work-cards (``.tide/arcs/works/NN-*/work.md``), file order."""
    from .arc import work as _work  # lazy import, same reason as above

    rows: List[Dict[str, str]] = []
    wd = _work.works_dir(Path(root))
    if not wd.is_dir():
        return rows
    for d in sorted(p for p in wd.iterdir() if p.is_dir()):
        f = d / "work.md"
        if not f.is_file():
            continue
        title = d.name
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    title = line[2:].strip() or d.name
                    break
        except OSError:
            continue
        status = (fields.read_field(f, "status") or "open").strip()
        rows.append({"dir": d.name, "title": title, "status": status})
    return rows


def _works_html(root: Path) -> str:
    rows = _work_rows(root)
    if not rows:
        return ""
    items = []
    for r in rows:
        st = r["status"] if r["status"] in ("open", "taken", "review", "done") else "open"
        items.append(
            '<li><span class="st st-{0}">{1}</span>{2}</li>'.format(
                st, _esc(r["status"]), _esc(r["title"])
            )
        )
    return '<ul class="works">{0}</ul>'.format("".join(items))


def render_page(home: Path) -> str:
    """Render the whole board page for control-home *home* (pure of the server).

    Sections: the home's own stream, then one section per **active** roster
    project (archived rows are hidden, as everywhere else on cold surfaces).
    A rostered path without ``.tide/`` says so instead of erroring.
    """
    from .launcher import menu as menu_mod  # lazy: menu pulls the launcher tree

    home = Path(home)
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts: List[str] = [
        _PAGE_TOP.format(refresh=_REFRESH_SECONDS, title=_esc(home.name)),
        "<h1>tide · {0}</h1>".format(_esc(home.name)),
        '<p class="meta">tide {0} · rendered {1} · reloads every {2}s</p>'.format(
            _esc(__version__), now, _REFRESH_SECONDS
        ),
        "<h2>control-home <span class=\"path\">{0}</span></h2>".format(_esc(str(home))),
        _stream_html(home),
        _works_html(home),
    ]

    entries = menu_mod.active_entries(roster_mod.read_roster(home))
    for e in entries:
        name, path = e.get("name", "?"), Path(e.get("path", ""))
        parts.append(
            '<h2>{0} <span class="path">{1}</span></h2>'.format(_esc(name), _esc(str(path)))
        )
        if not (path / ".tide").is_dir():
            parts.append(
                '<p class="empty">(no .tide/ here yet — adopt it: '
                "<code>tide adopt {0}</code>)</p>".format(_esc(str(path)))
            )
            continue
        parts.append(_stream_html(path))
        parts.append(_works_html(path))

    if not entries:
        parts.append(
            '<p class="empty">roster is empty — register a project: '
            "<code>tide roster add &lt;name&gt; &lt;path&gt;</code></p>"
        )
    return "\n".join(p for p in parts if p)


# --- the server ------------------------------------------------------------

def _make_handler(home: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — BaseHTTPRequestHandler API
            if self.path.split("?", 1)[0] not in ("/", "/index.html"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = render_page(home).encode("utf-8")
            except Exception as exc:  # noqa: BLE001 — a render bug must answer, not hang
                body = "tide board render error: {0}".format(exc).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # silence per-request stderr noise
            pass

    return Handler


def serve(home: Path, port: int = DEFAULT_PORT, bind: str = DEFAULT_BIND) -> ThreadingHTTPServer:
    """Bind the board server (without entering the serve loop); raises on a busy port."""
    try:
        return ThreadingHTTPServer((bind, port), _make_handler(Path(home)))
    except OSError as exc:
        raise BoardServeError(
            "cannot listen on {0}:{1} ({2}) — is another board already running? "
            "Pick a port: tide board --port <N>".format(bind, port, exc)
        )


# --- the living board ------------------------------------------------------

def _living_board() -> Path:
    """The living board's entry script, when this install carries it.

    Absent while the board is still being unpinned from one machine's paths;
    present once it ships as ``tide.board``. Either way this module keeps its
    own simple page as the fallback, so ``tide board`` always serves something.
    """
    return Path(__file__).resolve().parent / "board" / "serve_live.py"


def _serve_living(home: Path, args) -> int:
    """Hand the port to the living board (``tide.board.serve_live``).

    Spawned as a child, not imported: it is a script that re-executes its
    renderer as a subprocess on every request (so a broken render can never
    take the server down), and it wants to own its argv and its lifetime.
    ``tide board`` is the door here, not the engine.

    The home goes down as ``$TIDE_HOME`` rather than being guessed again on the
    other side: this process already resolved it, and two resolutions that can
    disagree are worse than one.
    """
    import os
    import subprocess
    import sys

    if args.bind not in ("", DEFAULT_BIND):
        print("tide board: the living board listens on {0} only — --bind {1} "
              "ignored (the phone path is `tailscale serve`)".format(
                  DEFAULT_BIND, args.bind))
    url = "http://{0}:{1}/".format(DEFAULT_BIND, args.port)
    print("tide board — {0}".format(home))
    print("  → {0}   (Ctrl-C to stop)".format(url))
    print("  " + quickstart.next_step_line("board"))
    proc = subprocess.Popen(
        [sys.executable, str(_living_board()), "--port", str(args.port)],
        env=dict(os.environ, TIDE_HOME=str(home)),
    )
    if getattr(args, "open", False):
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — no browser is not an error for a server
            pass
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 — a stuck child must not hang the exit
            proc.kill()
        print("\ntide board — stopped")
        return 0


# --- CLI wiring ------------------------------------------------------------

def _cmd_board(args) -> int:
    try:
        home = paths.control_home()
    except FileNotFoundError as exc:
        raise BoardServeError(str(exc))

    if getattr(args, "once", False):
        print(render_page(home))
        return 0

    # The living board when this install carries it, the simple page otherwise.
    # Absence is not an error: a build that ships without it must still serve
    # something, and a person asking for their board should get a board rather
    # than a lecture about a missing file.
    if not getattr(args, "plain", False) and _living_board().is_file():
        return _serve_living(home, args)

    httpd = serve(home, port=args.port, bind=args.bind)
    shown_host = "127.0.0.1" if args.bind in ("", "0.0.0.0") else args.bind
    url = "http://{0}:{1}/".format(shown_host, args.port)
    print("tide board — {0}".format(home))
    print("  → {0}   (Ctrl-C to stop)".format(url))
    print("  " + quickstart.next_step_line("board"))
    if getattr(args, "open", False):
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001 — no browser is not an error for a server
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ntide board — stopped")
    finally:
        httpd.server_close()
    return 0


def register(subparsers) -> None:
    """Add the top-level ``board`` command to *subparsers* (called by cli.py)."""
    p = subparsers.add_parser(
        "board",
        help="serve the board on localhost: home stream + every rostered project "
        "(+ work-cards when the work plugin is on)",
    )
    p.add_argument(
        "--port", type=int, default=DEFAULT_PORT,
        help="port to listen on (default {0})".format(DEFAULT_PORT),
    )
    p.add_argument(
        "--bind", default=DEFAULT_BIND,
        help="interface to bind (default {0} — localhost only; the phone path is "
        "`tailscale serve`, see docs/board.md)".format(DEFAULT_BIND),
    )
    p.add_argument(
        "--open", action="store_true", help="also open the page in the browser"
    )
    p.add_argument(
        "--once", action="store_true",
        help="render the page to stdout and exit (no server) — for checks",
    )
    p.add_argument(
        "--plain", action="store_true",
        help="serve the simple built-in page instead of the living board "
        "(fallback for a home where the living board will not render)",
    )
    p.set_defaults(func=_cmd_board, _cmd="board")
