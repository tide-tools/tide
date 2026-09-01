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

# --- the layer stays OUT of someone else's repository (работа 60) ------------
# The first hour on a fresh laptop: tide adopted a working repo and its own files
# showed up in that project's commit. The promise checked here, on a repo that
# already HAS history: no commit, no line in .gitignore, working tree as found —
# and the project still visible to the board.
mkdir -p ~/code/theirs && cd ~/code/theirs || exit 1
git init -q .
git config user.email theirs@example.com
git config user.name theirs
echo "print('their code')" > app.py
git add -A && git commit -qm "their own first commit" >/dev/null 2>&1
BEFORE="$(git rev-list --count HEAD)"

run "adopt a repo that has history" "" tide adopt --name theirs --no-orca

AFTER="$(git rev-list --count HEAD)"
[ "$AFTER" = "$BEFORE" ] && pass "not one commit added" "still $BEFORE" \
  || fail "not one commit added" "$BEFORE → $AFTER"
DIRT="$(git status --porcelain)"
[ -z "$DIRT" ] && pass "working tree exactly as found" \
  || fail "working tree exactly as found" "$(printf '%s' "$DIRT" | tr '\n' ' ')"
[ ! -e .gitignore ] && pass ".gitignore untouched (never created)" \
  || fail ".gitignore untouched (never created)" "$(cat .gitignore | tr '\n' ' ')"
grep -q '^/\.tide/$' .git/info/exclude \
  && pass "the exclusion is local to this machine" ".git/info/exclude" \
  || fail "the exclusion is local to this machine" "no /.tide/ in .git/info/exclude"
[ -d .tide ] && pass "the layer is there, just not in git" || fail "the layer is there, just not in git"
run "the board renders their project" "" tide status
say "tide layer says where it lives" "stays on this machine" tide layer

cd ~/control || exit 1
run "roster takes their project" "" tide roster add theirs /home/tester/code/theirs
run "board sees both projects"  "theirs" tide roster ls

# The way out for whoever already committed the layer: index cleared, files kept,
# history honestly left alone.
mkdir -p ~/code/burned && cd ~/code/burned || exit 1
git init -q .
git config user.email burned@example.com
git config user.name burned
mkdir -p .tide/canon && echo "# CANON.md — burned" > .tide/canon/CANON.md
echo "code" > app.py
git add -A && git commit -qm "the layer went in by accident" >/dev/null 2>&1
HEAD_WAS="$(git rev-parse HEAD)"

say "untrack says what it will do" "does NOT rewrite history" tide layer untrack

[ -z "$(git ls-files -- .tide/)" ] && pass "layer off the index" \
  || fail "layer off the index" "$(git ls-files -- .tide/ | tr '\n' ' ')"
[ -f .tide/canon/CANON.md ] && pass "every file still on disk" || fail "every file still on disk"
[ "$(git rev-parse HEAD)" = "$HEAD_WAS" ] && pass "history not rewritten" \
  || fail "history not rewritten" "HEAD moved"
git ls-tree -r --name-only HEAD | grep -q '^\.tide/' \
  && pass "old commit still carries it — as the command said" \
  || fail "old commit still carries it — as the command said"

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

# self-update must run end to end on a clone install: pull (a no-op here — the
# clone is already at the tip), gate, reinstall. Called EXACTLY as a person calls
# it — no --no-suite. That flag is why this check stayed green through the blocker
# it was supposed to catch (работа 57 п.6): it switched off the suite leg, which
# is the leg that refused every ordinary install, because a clone install has no
# pytest and the gate called that a failure instead of "not applicable".
# The expected substring also pins the honesty half: the gate must SAY the suite
# did not apply. A silent green would read as "the tests passed" when none ran.
run "self-update runs as a person calls it" "not applicable" tide self-update --force
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

# --- `tide board` itself: the desk, on a machine that has never seen tide -----
# The point of the release is that a person installs tide, opens the board and
# sees their DESK — threads, «сейчас от тебя», works, projects — not an empty
# page and not the 279-line offcut. Serving a pre-rendered file (below) proves
# the port works; it does not prove the verb does. So run the verb.
#
# On its own port: the pre-rendered page below owns $PORT, and two servers on
# one port is a flake, not a check.
# $TIDE_HOME, а не cd в дом: ровно так `tide init` и говорит человеку сделать
# («export TIDE_HOME=…, add to your shell profile»), и проверять надо тот путь,
# по которому его ведут. Мы стоим в проекте — оттуда доска обязана открываться.
# Едет ли живая доска В ПАКЕТЕ. Отдельной строкой, потому что её отсутствие
# деградирует тихо: `tide board` честно отдаёт простую страницу, и по одному
# только «сервер ответил 200» этого не увидеть.
# Питон ТОЙ установки, а не любой из PATH: tide живёт в venv, и голый python3
# про него не знает — на этом diagnostic уже один раз соврал «нет модуля».
# Берём интерпретатор из ШЕБАНГА самого `tide` — он и есть тот питон, которым
# команда исполняется, где бы venv ни лежал. Сосед по каталогу врёт: `tide`
# бывает шимом в ~/.local/bin, рядом с которым питона нет вовсе.
TPY="$(head -1 "$(command -v tide)" | sed 's/^#!//; s/ .*//')"
[ -x "$TPY" ] || TPY=python3
lives="$("$TPY" -c 'import tide.board_server as b; p=b._living_board(); print(("yes" if p.is_file() else "no"), p)' 2>&1 || true)"
case "$lives" in
  yes*) pass "the living board rides in the package" ;;
  *)    fail "the living board rides in the package" "$lives" ;;
esac

VPORT=$((PORT + 1))
TIDE_HOME=~/control tide board --port "$VPORT" >/tmp/tideboard.log 2>&1 &
VERB_PID=$!
verb=""
for _ in $(seq 1 30); do
  verb_body="$(curl -fsS --max-time 20 "http://127.0.0.1:$VPORT/" 2>/dev/null || true)"
  [ -n "$verb_body" ] && { verb=yes; break; }
  sleep 1
done
if [ -z "$verb" ]; then
  fail "tide board serves" "$(tail -2 /tmp/tideboard.log 2>/dev/null | tr '\n' ' ')"
else
  pass "tide board serves" "on :$VPORT"
  # Что именно приехало. Ищем ряд вкладок и стол — две вещи, которых у обрезка
  # нет; их отсутствие означает, что человек открыл доску и не нашёл на ней
  # своего стола, а это ровно тот провал, ради которого проверка и написана.
  #
  # Сравнение БАШЕМ, а не `... | grep -q`: в этом файле стоит `pipefail`, а
  # `grep -q` закрывает вход на первом же совпадении — и `printf` слева умирает
  # от SIGPIPE. Конвейер получает 141, проверка читает это как «не нашли» и
  # честно врёт на полностью исправной странице. Оплачено полутора часами.
  case "$verb_body" in
    *'class="vtab'*) pass "the board has its tab row" ;;
    *) fail "the board has its tab row" "no tab row in $(printf '%s' "$verb_body" | wc -c) bytes" ;;
  esac
  case "$verb_body" in
    *'id="issues"'*) pass "the desk is there (issues)" ;;
    *) fail "the desk is there (issues)" "log: $(tail -3 /tmp/tideboard.log 2>/dev/null | tr '\n' ' ' | head -c 200)" ;;
  esac
  # Плагины свежей установки: стол и работы есть, чужое личное — нет.
  case "$verb_body" in
    *'data-v="news"'*) fail "one person's own tabs stay off" "news tab shipped to a fresh home" ;;
    *) pass "one person's own tabs stay off" ;;
  esac
fi
kill "$VERB_PID" 2>/dev/null || true

# --- the browser leg: a board served over a published port -------------------
# A pre-rendered page on the published port, so a human running `--board` has
# something to look at even when the verb above is the thing under repair. What
# this proves is that the port reaches the host.
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
