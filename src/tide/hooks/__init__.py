"""tide.hooks — Claude Code hook wiring (install + runtime hooks).

Modules (U10):
  install.py        — `tide install-hooks`: write SessionStart + PreToolUse
                      entries, MERGE-not-clobber existing hooks (e.g. rtk).
  session_start.py  — `tide hook session-start`: print board + role reminder +
                      canon-drift / unmerged-delta warnings.
  edit_gate.py      — `tide hook edit-gate` (PreToolUse): block project edits
                      until a worker arc is open; allow edits inside .tide/;
                      SKIP closed __…__ dirs; NEVER grep -r.
  role_gate.py      — `tide hook role-gate` (PreToolUse): forbid the orchestrator
                      (HEAD) from doing worker-work (Write/Edit/NotebookEdit and
                      mutating Bash); pure no-op for workers and unset role.
  ask_gate.py       — `tide hook ask-gate` (PreToolUse, AskUserQuestion): in an AO
                      session with no live human reply for 10 min, deny the dialog
                      and send the question to the manager (SOC-108).
  perm_notify.py    — `tide hook perm-notify` (PermissionRequest): in an AO session,
                      report the pending permission dialog to the human's Telegram;
                      never decides (SOC-108). Not wired by install-hooks.
  orphan.py         — runs on `tide hook handoff-confirm`: a thread whose head is dead
                      in AO with no open offer → one line to the manager; autoraise
                      behind TIDE_ORPHAN_AUTORAISE / orphan-autoraise=on (SOC-167).

``cli.py`` wires the human ``install-hooks`` command and the internal ``hook``
dispatch group via :func:`tide.hooks.install.register` /
:func:`tide.hooks.install.register_hook_group`.
"""
