"""tide.board — the living board, in the box.

This is the board its author actually works on every day, moved into the package
so that a person who installs tide gets the same surface: threads, the inbox
desk with its «сейчас от тебя» card, works, projects — not the 279-line offcut that
:mod:`tide.board_server` serves (that one stays as the fallback for a home
where this fails to render).

Two modules, both stdlib-only, both plain scripts rather than importable
libraries — they are run, not imported, and :mod:`serve_live` re-executes
:mod:`live_projection` as a subprocess on every request so a broken render can
never take the server down with it:

* ``live_projection.py`` — the whole page, built from files on every render.
* ``serve_live.py``      — the HTTP door and the buttons behind it.

Where it reads from is resolved, not nailed down: ``$TIDE_HOME`` (or a climb for
``roster.md``) says which home, and the roster says which projects — including
where each project keeps its works. Nothing here knows a path on one machine.

The files travel with the package as data (``scope/index.html``, ``shell/``),
declared in ``pyproject.toml`` — install it and the page has its template.
"""
