#!/usr/bin/env bash
#
# smoke.sh — zero to an open board, on a machine that has never seen tide.
#
# Runs INSIDE the container (see Dockerfile). Every step prints PASS or FAIL and
# the run keeps going after a failure, so one command tells you everything that is
# broken rather than only the first thing.
#
#   default        run every check, print the verdict, exit 0/1
#   board          the same, then KEEP the board served on :8765 so a human can
#                  open it in a browser on the host
set -uo pipefail

MODE="${1:-check}"
PORT=8765
HOST_PORT="${HOST_PORT:-$PORT}"   # what the run.sh -p mapping calls it on the host
FAILS=0
STEP=0

pass() { STEP=$((STEP+1)); printf '  \033[32m✓\033[0m %-38s %s\n' "$1" "${2:-}"; }
fail() { STEP=$((STEP+1)); FAILS=$((FAILS+1)); printf '  \033[31m✗\033[0m %-38s %s\n' "$1" "${2:-}"; }
# run <name> <expected-substring> <cmd...> — a step that must exit 0.
run()  { _step 0 "$@"; }
# say <name> <expected-substring> <cmd...> — a step judged ONLY on what it says.
# Some honest answers are non-zero: `--rollback --dry-run` exits 2 when there is
# no recovery point yet, which is the correct report on a fresh install.
say()  { _step any "$@"; }

_step() {
  local want_rc="$1" name="$2" want="$3"; shift 3
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [ "$want_rc" = "0" ] && [ $rc -ne 0 ]; then
    fail "$name" "exit $rc: $(printf '%s' "$out" | tail -2 | tr '\n' ' ')"
  elif [ -n "$want" ] && ! printf '%s' "$out" | grep -q -- "$want"; then
    fail "$name" "no '$want' in: $(printf '%s' "$out" | head -2 | tr '\n' ' ')"
  else
    # cut -c counts BYTES: a 58-byte cut through Russian output tears a UTF-8
    # character in half. Python slices characters.
    pass "$name" "$(printf '%s' "$out" | head -1 | python3 -c 'import sys; print(sys.stdin.readline().rstrip("\n")[:58])')"
  fi
}

echo
echo "tide release check — clean Linux machine, installed the way people install: git clone + ./install.sh"
echo

# --- the floor --------------------------------------------------------------
run "python floor"            "3.12"  python3 --version
run "tide on PATH"            "tide"  tide --version
run "tide help"               "arc"   tide help

# --- a control-home ---------------------------------------------------------
mkdir -p ~/control && cd ~/control || exit 1
run "tide init"               ""      tide init --name control
[ -f ~/control/roster.md ] && pass "roster.md exists" || fail "roster.md exists"
[ -d ~/control/.tide ]     && pass ".tide/ unfolded"  || fail ".tide/ unfolded"

# --- a real project ---------------------------------------------------------
mkdir -p ~/code/demo && cd ~/code/demo || exit 1
run "tide adopt"              ""      tide adopt --name demo --goal "a demo project" --no-orca
[ -d ~/code/demo/.tide ] && pass "project .tide/ exists" || fail "project .tide/ exists"

cd ~/control || exit 1
run "tide roster add"         ""      tide roster add demo /home/tester/code/demo
run "tide roster ls"          "demo"  tide roster ls

# --- the board + a unit of work ---------------------------------------------
cd ~/code/demo || exit 1
run "tide status (the board)" ""      tide status
run "tide arc new"            ""      tide arc new first-light
run "tide candidate"          ""      tide candidate add "something to do later"
run "tide doctor"             "doctor" tide doctor --no-network

# --- the released-install paths (the point of this whole work) ---------------
# This is a CLONE install (git clone + install.sh — the front door), so the newer
# tide lives on the remote and self-update has to pull before it can judge.
# install.sh stamps the marker, so a FRESH install must read as current — not
# nag about the very commit it was just installed from.
run "fresh install reads current" "current" tide self-update --check
run "check knows it is a clone" "local checkout" tide self-update --check
run "dry-run offers the pull" "would pull"  tide self-update --dry-run
run "dry-run names the reinstall" "would run" tide self-update --dry-run
say "rollback is inspectable" "rollback"    tide self-update --rollback --dry-run

# self-update must be able to actually run end to end on a clone install: pull
# (a no-op here — the clone is already at the tip), gate, reinstall. --no-suite
# keeps it to the portable gate so the check stays fast enough to run often.
run "self-update runs on a clone" "" tide self-update --force --no-suite
run "tide report --dry-run"   "what happened" tide report --dry-run --no-network "docker smoke"

# The report must not carry this machine's home path off the machine.
if tide report --dry-run --no-network "docker smoke" 2>&1 | grep -q "/home/tester"; then
  fail "report withholds home paths" "found /home/tester in the body"
else
  pass "report withholds home paths"
fi

# --- the SECONDARY channel: the release artifact is pip-installable ----------
# Homebrew is not the front door, but its formula pip-installs the release sdist,
# so the artifact `tide release` builds must install cleanly on its own.
( cd ~/tide && git archive --format=tar.gz --prefix="tide-check/" HEAD -o /tmp/artifact.tar.gz )
python3 -m venv /tmp/secondary >/dev/null 2>&1
if /tmp/secondary/bin/pip install --quiet /tmp/artifact.tar.gz >/tmp/pip.log 2>&1 \
   && /tmp/secondary/bin/tide --version >/dev/null 2>&1; then
  pass "release artifact pip-installs" "$(/tmp/secondary/bin/tide --version)"
else
  fail "release artifact pip-installs" "$(tail -2 /tmp/pip.log | tr '\n' ' ')"
fi

# --- the browser leg: a board served over a published port -------------------
# The engine ships no HTTP server of its own, so we render the board it DOES
# produce (tide status) into a page and serve it. What this proves is the part
# that actually breaks in a container: that a freshly installed tide produces a
# real board, and that the port reaches the host.
BOARD=~/board
mkdir -p "$BOARD"
{
  echo '<!doctype html><meta charset="utf-8"><title>tide board</title>'
  echo '<style>body{background:#111;color:#eee;font:14px ui-monospace,monospace;padding:2rem}'
  echo 'h1{font-size:1rem;color:#9cf;letter-spacing:.08em}pre{white-space:pre-wrap}</style>'
  echo "<h1>tide $(tide --version 2>/dev/null) — demo</h1><pre>"
  (cd ~/code/demo && tide status 2>&1) | sed 's/&/\&amp;/g; s/</\&lt;/g'
  echo '</pre>'
} > "$BOARD/index.html"

python3 -m http.server "$PORT" --bind 0.0.0.0 --directory "$BOARD" >/tmp/board.log 2>&1 &
BOARD_PID=$!

# Poll rather than sleep-and-hope: a fixed sleep is the classic flaky check, and
# this one runs on whatever machine happens to be free.
served=""
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$PORT/" 2>/dev/null | grep -q "tide board"; then served=yes; break; fi
  sleep 0.5
done
if [ -n "$served" ]; then
  pass "board served on :$PORT"
else
  fail "board served on :$PORT" "no HTTP 200: $(tail -2 /tmp/board.log 2>/dev/null | tr '\n' ' ')"
fi

echo
if [ "$FAILS" -eq 0 ]; then
  printf '\033[32m✓ %d checks, all green — the release installs and works on a clean machine\033[0m\n' "$STEP"
else
  printf '\033[31m✗ %d of %d checks FAILED\033[0m\n' "$FAILS" "$STEP"
fi
echo "  not checked here (mac-only): launchd service, terminal spawning, Claude Code, keychain"

if [ "$MODE" = "board" ]; then
  echo
  echo "  board is live → open http://localhost:$HOST_PORT on your machine (ctrl-c to stop)"
  wait "$BOARD_PID"
fi

kill "$BOARD_PID" 2>/dev/null
[ "$FAILS" -eq 0 ]
