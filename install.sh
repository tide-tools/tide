#!/usr/bin/env bash
#
# install.sh — put the `tide` CLI on your PATH, under a Python ≥ 3.12 interpreter.
#
#   - Uses `pipx` when available (isolated app install).
#   - Otherwise falls back to a dedicated venv + a symlink into a PATH dir.
#   - Idempotent: re-running upgrades in place, never errors on "already there".
#   - Prints the resulting `tide --version` at the end.
#
# No PyPI involved — this installs THIS checkout. Run it from anywhere:
#   ./install.sh
#
set -euo pipefail

# --- locate the repo (this script's dir) ------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$SCRIPT_DIR"

# --- config (overridable via env) -------------------------------------------
# NB: $TIDE_HOME is NOT used here on purpose — to the tide CLI that variable
# means "your control-home" (the dir with roster.md). Reusing it as the venv
# location would dump a venv into someone's control-home the moment they set
# TIDE_HOME the way the docs tell them to. The install dir has its own knob.
INSTALL_DIR="${TIDE_INSTALL_DIR:-$HOME/.local/share/tide}"
VENV_DIR="$INSTALL_DIR/venv"
BIN_DIR="${TIDE_BIN_DIR:-$HOME/.local/bin}"
MIN_MAJOR=3
MIN_MINOR=12

say()  { printf '\033[1m›\033[0m %s\n' "$*"; }
warn() { printf '\033[33m! %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

# --- find a Python >= 3.12 ---------------------------------------------------
# Returns the interpreter path on stdout, or empty if none qualifies.
py_ok() {
  local py="$1"
  command -v "$py" >/dev/null 2>&1 || return 1
  "$py" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= ($MIN_MAJOR,$MIN_MINOR) else 1)" >/dev/null 2>&1
}

find_python() {
  local candidates=("python3.12" "python3.13" "python3.14" "python3" "python")
  if [ -n "${TIDE_PYTHON:-}" ]; then
    candidates=("$TIDE_PYTHON" "${candidates[@]}")
  fi
  local py
  for py in "${candidates[@]}"; do
    if py_ok "$py"; then
      command -v "$py"
      return 0
    fi
  done
  return 1
}

PYTHON="$(find_python || true)"
[ -n "$PYTHON" ] || die "no Python ≥ ${MIN_MAJOR}.${MIN_MINOR} found. Install one (e.g. \`brew install python@3.12\`) or set TIDE_PYTHON=/path/to/python."
PYVER="$("$PYTHON" -c 'import sys;print(".".join(map(str,sys.version_info[:3])))')"
say "using Python $PYVER ($PYTHON)"

# --- install path A: pipx ----------------------------------------------------
install_via_pipx() {
  say "pipx detected → installing (isolated app)"
  # --force makes this idempotent: reinstall over any existing copy.
  pipx install --force --python "$PYTHON" "$REPO_DIR"
  pipx ensurepath >/dev/null 2>&1 || true
}

# --- install path B: venv + symlink -----------------------------------------
install_via_venv() {
  say "no pipx → installing into a dedicated venv ($VENV_DIR)"

  # Heal a half-built venv. A previous run can die mid-way (ensurepip failing
  # on a broken local Python is the lived case) and leave a venv WITHOUT pip;
  # every re-run would then hit "No module named pip" forever. A venv whose
  # python can't run pip is scrap — say so in one line and rebuild it.
  if [ -x "$VENV_DIR/bin/python" ] \
      && ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    say "existing venv has no working pip (a previous install died half-way) → rebuilding it"
    rm -rf "$VENV_DIR"
  fi

  if [ ! -x "$VENV_DIR/bin/python" ]; then
    mkdir -p "$INSTALL_DIR"
    local venv_log="$INSTALL_DIR/venv-create.log"
    if ! "$PYTHON" -m venv "$VENV_DIR" >"$venv_log" 2>&1; then
      # Don't leave the half-built venv behind — the next run starts clean.
      rm -rf "$VENV_DIR"
      warn "your Python can't build a venv ($PYTHON — its ensurepip/venv machinery is broken)."
      warn "fix: install python.org 3.12 or \`brew reinstall python@3.12\`, then re-run ./install.sh"
      if [ -n "${TIDE_DEBUG:-}" ]; then
        cat "$venv_log" >&2
      else
        warn "full error kept in $venv_log (or re-run with TIDE_DEBUG=1 to see it)"
      fi
      exit 1
    fi
    rm -f "$venv_log"
  fi
  # quiet, idempotent install/upgrade of THIS checkout into the venv
  "$VENV_DIR/bin/python" -m pip install --upgrade pip >/dev/null
  "$VENV_DIR/bin/python" -m pip install --upgrade "$REPO_DIR"

  mkdir -p "$BIN_DIR"
  ln -sf "$VENV_DIR/bin/tide" "$BIN_DIR/tide"
  say "linked $BIN_DIR/tide → $VENV_DIR/bin/tide"

  case ":$PATH:" in
    *":$BIN_DIR:"*) : ;;
    *) warn "$BIN_DIR is not on your PATH. Add this to your shell profile:"
       warn "    export PATH=\"$BIN_DIR:\$PATH\"" ;;
  esac
}

if command -v pipx >/dev/null 2>&1; then
  install_via_pipx
else
  install_via_venv
fi

# --- verify ------------------------------------------------------------------
say "verifying…"
TIDE_BIN=""
if command -v tide >/dev/null 2>&1; then
  TIDE_BIN="$(command -v tide)"
elif [ -x "$BIN_DIR/tide" ]; then
  TIDE_BIN="$BIN_DIR/tide"
fi

if [ -n "$TIDE_BIN" ]; then
  VERSION_OUT="$("$TIDE_BIN" --version 2>&1 || true)"
  say "installed: $TIDE_BIN"
  printf '\033[32m✓ %s\033[0m\n' "$VERSION_OUT"

  # Record WHAT we just installed. Without this the install has no marker, so
  # tide compares the bare metadata version against the checkout's commit, they
  # never match, and a brand-new install greets you with "update available" —
  # pointing at the very commit you just installed. Best-effort: a machine that
  # cannot write the marker still has a working tide.
  "$TIDE_BIN" self-update --stamp >/dev/null 2>&1 || true

  # One gesture per step from here on — say the next one out loud.
  echo
  say "next: make yourself a control-home (the one place you lead from):"
  printf '      mkdir ~/tide-home && cd ~/tide-home && tide init --git\n'
  say "then open the board:  tide board --open"
else
  warn "tide is installed but not yet on PATH for this shell — open a new shell or fix PATH (see above)."
  warn "direct check: $VENV_DIR/bin/tide --version"
  exit 0
fi
