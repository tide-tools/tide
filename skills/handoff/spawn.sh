#!/usr/bin/env bash
# spawn.sh — open a NEW terminal inside Orca and boot a fresh `claude` session
# seeded with a handoff file. Drives Orca's UI via osascript (Cmd-T + keystroke).
#
# Usage: spawn.sh <project_dir> <seed_path> [extra_claude_args...]
#   env CLOSE_OLD=0 to disable closing the source (old) session after handoff.
#
# Precondition: Accessibility access granted to Orca Helper.app, else macOS blocks
# keystroke injection (error 1002). One-time, set in System Settings.

set -euo pipefail

PROJECT="${1:?need project dir}"
SEED="${2:?need seed path}"
shift 2 || true
EXTRA_ARGS=("$@")
CLOSE_OLD="${CLOSE_OLD:-1}"   # close the source session's Orca tab after handoff (default on)

[ -d "$PROJECT" ] || { echo "spawn.sh: project dir not found: $PROJECT" >&2; exit 1; }
[ -f "$SEED" ]    || { echo "spawn.sh: seed file not found: $SEED" >&2; exit 1; }

# Find the OLD (source) Orca tab's root login shell: walk up the PPID chain from here
# until we hit the process whose parent is "Orca Helper" — that process IS the tab's
# shell. Killing it collapses the old tab (claude child dies with it). Empty if not found.
find_tab_shell_pid() {
  local pid=$$ ppid pcomm
  local i=0
  while [ "$i" -lt 16 ]; do
    ppid="$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')"
    [ -z "$ppid" ] && { echo ""; return; }
    pcomm="$(ps -o comm= -p "$ppid" 2>/dev/null)"
    case "$pcomm" in *"Orca Helper"*) echo "$pid"; return ;; esac
    [ "$ppid" -le 1 ] && { echo ""; return; }
    pid="$ppid"; i=$((i+1))
  done
  echo ""
}
OLD_PID=""
[ "$CLOSE_OLD" = "1" ] && OLD_PID="$(find_tab_shell_pid)"

# Runtime addendum: a close-old directive appended to the first message (NOT to the
# canonical seed file — kept pristine). The fresh session, once it confirms it is alive,
# kills the old tab's shell. mktemp so the seed on disk stays clean.
RUNTIME=""
if [ -n "$OLD_PID" ]; then
  RUNTIME="$(mktemp -t handoff-runtime)"
  {
    echo ""
    echo "---"
    echo "## ⟦handoff-runtime⟧ close-old (новая гасит старую)"
    echo "Ты — свежая сессия. КАК ТОЛЬКО убедился, что поднялся и сориентировался по сиду,"
    echo "ПЕРВЫМ делом закрой старую сессию-источник этого хендоффа — её Orca-таб:"
    echo ""
    echo '```bash'
    echo "kill -HUP $OLD_PID   # SIGHUP, не SIGTERM: интерактивный zsh -l игнорит TERM; HUP = сигнал закрытия терминала → shell рвётся, claude-источник умирает с ним → таб закрывается"
    echo '```'
    echo ""
    echo "Это штатная часть тёплой передачи: одна живая нить, без мёртвых табов. Если"
    echo "процесс уже мёртв (kill вернёт ошибку) — просто игнорь, цель достигнута."
  } > "$RUNTIME"
fi

# Build a tiny launch script so we only have to TYPE a short, escape-free command
# into the new Orca terminal. The launch script holds the cd + full claude invocation.
LAUNCH="$(mktemp -t handoff-launch)"
{
  echo '#!/usr/bin/env bash'
  # NB: no OSC tab-title — Orca/claude derive the tab title from the chat theme and clobber
  # any ANSI title we set (proven dead 2026-06-23, hand­off #2). The session theme is already
  # arc-relevant, so we let it stand. Don't re-add OSC here.
  echo "cd $(printf '%q' "$PROJECT")"
  printf 'exec claude --dangerously-skip-permissions'
  for a in ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}; do printf ' %q' "$a"; done
  # first message = seed (+ runtime close-old addendum, concatenated at launch time)
  if [ -n "$RUNTIME" ]; then
    printf ' "$(cat %q; cat %q)"\n' "$SEED" "$RUNTIME"
  else
    printf ' "$(cat %q)"\n' "$SEED"
  fi
} > "$LAUNCH"
chmod +x "$LAUNCH"

# Open the new tab. PREFERRED: the Orca CLI (`orca terminal create --command …`) — it spins up a
# real terminal tab running the command directly, with NO keystroke injection. That sidesteps every
# osascript-keystroke failure mode (Cmd-T not landing, focus races, paste timing on a not-yet-ready
# `zsh -l`, Cyrillic keyboard layout) — the flakiness that made spawns silently no-op. FALLBACK:
# the legacy osascript activate→Cmd-T→paste→Return dance, used only if the CLI isn't reachable.
SPAWN_METHOD=""
if command -v orca >/dev/null 2>&1 && orca status >/dev/null 2>&1; then
  # Select the worktree by EXPLICIT PATH, not `active`. `active` resolves to the UI-focused
  # worktree — empty when the caller has no GUI focus (e.g. the launchd pulse daemon driving the
  # self-feeding pool), so `create` would fail and silently drop to the TCC-blocked osascript
  # fallback (arc 36 crit-8 root cause, 2026-06-24). The path selector is context-independent: the
  # orca CLI reaches the Orca runtime via its well-known socket regardless of env/GUI, and Orca.app
  # opens the visible tab itself — no keystroke, no Accessibility/Automation grant, works headless.
  if orca terminal create --worktree "path:$PROJECT" --command "bash $(printf '%q' "$LAUNCH")" >/dev/null 2>&1; then
    SPAWN_METHOD="orca-cli"
  fi
fi
if [ -z "$SPAWN_METHOD" ]; then
  # Fallback: clipboard paste (immune to keyboard layout + dropped leading chars) + keystrokes.
  printf 'bash %s' "$LAUNCH" | pbcopy
  osascript <<OSA
tell application "Orca" to activate
delay 0.4
tell application "System Events"
  keystroke "t" using command down
  delay 1.8
  key code 36
  delay 0.6
  keystroke "v" using command down
  delay 0.3
  key code 36
end tell
OSA
  SPAWN_METHOD="osascript"
fi

echo "spawn.sh: launched claude in a new Orca terminal (via $SPAWN_METHOD)"
echo "  project : $PROJECT"
echo "  seed    : $SEED"
echo "  launcher: $LAUNCH"
[ -n "$OLD_PID" ] && echo "  close-old: new session will kill old tab-shell pid=$OLD_PID" \
                   || echo "  close-old: skipped (CLOSE_OLD=$CLOSE_OLD or pid not found)"
