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
TIDE_HOME="${TIDE_HOME:-$HOME/.local/share/tide}"
VENV_DIR="$TIDE_HOME/venv"
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
  if [ ! -x "$VENV_DIR/bin/python" ]; then
    mkdir -p "$TIDE_HOME"
    "$PYTHON" -m venv "$VENV_DIR"
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
else
  warn "tide is installed but not yet on PATH for this shell — open a new shell or fix PATH (see above)."
  warn "direct check: $VENV_DIR/bin/tide --version"
  exit 0
fi
