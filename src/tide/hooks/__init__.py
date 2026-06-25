"""tide.hooks — Claude Code hook wiring (install + runtime hooks).

Modules (U10):
  install.py        — `tide install-hooks`: write SessionStart + PreToolUse
                      entries, MERGE-not-clobber existing hooks (e.g. rtk).
  session_start.py  — `tide hook session-start`: print board + role reminder +
                      cannon-drift / unmerged-delta warnings.
  edit_gate.py      — `tide hook edit-gate` (PreToolUse): block project edits
                      until a worker arc is open; allow edits inside .tide/;
                      SKIP closed __…__ dirs; NEVER grep -r.
  role_gate.py      — `tide hook role-gate` (PreToolUse): forbid the orchestrator
                      (HEAD) from doing worker-work (Write/Edit/NotebookEdit and
                      mutating Bash); pure no-op for workers and unset role.

``cli.py`` wires the human ``install-hooks`` command and the internal ``hook``
dispatch group via :func:`tide.hooks.install.register` /
:func:`tide.hooks.install.register_hook_group`.
"""
