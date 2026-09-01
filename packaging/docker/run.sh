#!/usr/bin/env bash
#
# run.sh — one command: does this release install and work on a clean machine?
#
#   packaging/docker/run.sh              check the WORKTREE (what you are editing now)
#   packaging/docker/run.sh --board      the same, then leave the board open on
#                                        http://localhost:8765 for you to look at
#   packaging/docker/run.sh --port 9000  serve that board on another host port
#                                        (only --board publishes a port at all)
#   packaging/docker/run.sh --ref HEAD   check a commit or tag instead — with --ref
#                                        the artifact is byte-identical to what
#                                        `tide release` would publish for that ref
#
# The default is the worktree on purpose: checking only committed code means every
# fix costs a commit before you can see whether it worked, which is how a check
# stops being run. Cheap enough to run often — the apt + venv layers cache, so a
# re-run after a code change is the pip install and the checks, nothing more.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
REF=""          # empty = the worktree
MODE="check"
PORT=8765

while [ $# -gt 0 ]; do
  case "$1" in
    --board) MODE="board"; shift ;;
    --port)  PORT="$2"; shift 2 ;;
    --ref)   REF="$2"; shift 2 ;;
    -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed — this check needs it. Everything else in the" >&2
  echo "release path works without docker; only this clean-machine check does not." >&2
  exit 127
fi
if ! docker info >/dev/null 2>&1; then
  echo "docker is installed but not running — start Docker Desktop and re-run." >&2
  exit 127
fi

# Say a busy port up front, in words — not as docker noise after minutes of
# building. Only --board publishes a port; a plain check needs none.
if [ "$MODE" = "board" ]; then
  if ! python3 -c 'import socket,sys
s = socket.socket()
try: s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError: raise SystemExit(1)
finally: s.close()' "$PORT" 2>/dev/null; then
    echo "port $PORT is already taken on this machine (another board or app is listening)." >&2
    echo "pick a free one: packaging/docker/run.sh --board --port 9000" >&2
    exit 2
  fi
fi

VERSION="$(cd "$REPO" && python3 -c 'import re,sys; print(re.search(r"^\s*version\s*=\s*\"([^\"]+)\"", open("pyproject.toml").read(), re.M).group(1))')"
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT

# The source under test travels as a git BUNDLE, so the container gets a real
# clone with an origin — which is what makes the front door (git clone +
# install.sh) and self-update's clone path testable rather than simulated.
STAGE="$CTX/stage"
mkdir -p "$STAGE"

if [ -n "$REF" ]; then
  echo "› bundling $REF (tide $VERSION)"
  git -C "$REPO" archive --format=tar --prefix="" "$REF" | tar -x -C "$STAGE"
else
  echo "› bundling the worktree (tide $VERSION) — uncommitted AND untracked files included"
  # Checking only committed code would mean every fix costs a commit before you
  # can see whether it worked, which is how a check stops being run. The file
  # SET is what really lies in the working tree: tracked + untracked (a new
  # module not yet `git add`ed must not silently vanish from the check — that
  # once hid 17 files); only .gitignore'd junk (.tide/, build/, caches) stays out.
  ( cd "$REPO" && git ls-files -co --exclude-standard ) | while IFS= read -r f; do
      [ -f "$REPO/$f" ] || continue
      mkdir -p "$STAGE/$(dirname "$f")"
      cp "$REPO/$f" "$STAGE/$f"
    done
fi

# A throwaway repo, so the real one is never written to: no commit, no stash, no
# index touched. add -A -f: everything physically staged rides into the bundle —
# the stage copy of .gitignore must not re-hide a file we deliberately staged.
git -C "$STAGE" init --quiet -b main
git -C "$STAGE" add -A -f
git -C "$STAGE" -c user.email=check@local -c user.name=check \
    commit --quiet -m "tide $VERSION under test"
git -C "$STAGE" bundle create "$CTX/tide.bundle" main >/dev/null 2>&1

cp "$HERE/Dockerfile" "$HERE/smoke.sh" "$CTX/"
echo "  $(cd "$CTX" && wc -c < tide.bundle) bytes"

echo "› building the clean machine (the first build takes a couple of minutes; re-runs reuse the cache)"
docker build --quiet -t tide-release-check "$CTX" >/dev/null

# The container serves its board on 8765 internally; only --board maps it to the
# host. The check mode talks to nobody outside, so it publishes nothing — a busy
# host port must not be able to kill a plain check (finding 4, release panel).
echo "› running the checks"
if [ "$MODE" = "board" ]; then
  docker run --rm -it -p "$PORT:8765" -e HOST_PORT="$PORT" tide-release-check board
else
  docker run --rm tide-release-check check
fi
