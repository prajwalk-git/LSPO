#!/usr/bin/env bash
#
# install.sh — installs rename_paper.py and makes it usable as `rename-paper`
# from any directory, in any new or existing terminal.
#
# What it does:
#   1. Checks for python3 and pip.
#   2. Installs required Python dependencies (pdfplumber, requests, pypdf).
#   3. Copies rename_paper.py to ~/bin/rename_paper (safe, permanent location).
#   4. Adds ~/bin to PATH in your shell rc file, if not already present.
#   5. Sources the rc file so it's ready to use immediately in THIS terminal
#      (new terminals will pick it up automatically).
#
# Usage:
#   ./install.sh
#
# Must be run from the same folder as rename_paper.py (or edit SCRIPT_SRC below).

set -euo pipefail

INSTALL_DIR="$HOME/bin"
SCRIPT_NAME="rename_paper.py"
INSTALLED_NAME="rename-paper"

# Raw URL used ONLY as a fallback when this installer is run standalone
# (e.g. via `curl | bash`) and rename_paper.py isn't sitting next to it.
# EDIT THIS after you push your repo to GitHub.
REMOTE_RAW_URL="https://raw.githubusercontent.com/prajwalk-git/LSPO/main/rename_paper.py"

# --------------------------------------------------------------------------
# 0. Locate the source script: prefer a local copy next to this installer
#    (normal case when the repo was cloned); fall back to downloading it
#    (case when install.sh was piped straight from curl with no clone).
# --------------------------------------------------------------------------
# BASH_SOURCE[0] is unreliable when read from a pipe (curl | bash), so guard it.
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
else
    SCRIPT_DIR=""
fi

SCRIPT_SRC="${SCRIPT_DIR:+$SCRIPT_DIR/$SCRIPT_NAME}"

if [[ -n "$SCRIPT_SRC" && -f "$SCRIPT_SRC" ]]; then
    echo "[ok] found $SCRIPT_NAME locally"
else
    echo "[info] $SCRIPT_NAME not found locally — downloading from GitHub..."
    TMP_DOWNLOAD="$(mktemp /tmp/rename_paper.XXXXXX.py)"
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$REMOTE_RAW_URL" -o "$TMP_DOWNLOAD"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$REMOTE_RAW_URL" -O "$TMP_DOWNLOAD"
    else
        echo "ERROR: neither curl nor wget is available to download $SCRIPT_NAME."
        exit 1
    fi
    if [[ ! -s "$TMP_DOWNLOAD" ]]; then
        echo "ERROR: download failed or file is empty. Check REMOTE_RAW_URL in install.sh."
        exit 1
    fi
    SCRIPT_SRC="$TMP_DOWNLOAD"
    echo "[ok] downloaded $SCRIPT_NAME"
fi

echo "== rename-paper installer =="
echo

# --------------------------------------------------------------------------
# 1. Check for python3 and pip
# --------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 is not installed. Install it first, e.g.:"
    echo "  sudo apt update && sudo apt install python3 python3-pip"
    exit 1
fi
echo "[ok] python3 found: $(command -v python3)"

if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "ERROR: pip is not available for python3. Install it first, e.g.:"
    echo "  sudo apt install python3-pip"
    exit 1
fi
echo "[ok] pip found"
echo

# --------------------------------------------------------------------------
# 2. Install Python dependencies
# --------------------------------------------------------------------------
echo "Installing Python dependencies (pdfplumber, requests, pypdf)..."
if python3 -m pip install --break-system-packages -q pdfplumber requests pypdf 2>/tmp/rename_paper_pip_err.log; then
    echo "[ok] dependencies installed"
else
    echo "[warn] install with --break-system-packages failed, trying without it..."
    if python3 -m pip install -q pdfplumber requests pypdf 2>>/tmp/rename_paper_pip_err.log; then
        echo "[ok] dependencies installed"
    else
        echo "ERROR: pip install failed. See details below:"
        cat /tmp/rename_paper_pip_err.log
        echo
        echo "You may need to install them manually, e.g. inside a virtualenv:"
        echo "  python3 -m venv ~/.venvs/rename-paper"
        echo "  source ~/.venvs/rename-paper/bin/activate"
        echo "  pip install pdfplumber requests pypdf"
        exit 1
    fi
fi
echo

# --------------------------------------------------------------------------
# 3. Copy the script to a safe, permanent location
# --------------------------------------------------------------------------
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_SRC" "$INSTALL_DIR/$INSTALLED_NAME"
chmod +x "$INSTALL_DIR/$INSTALLED_NAME"
echo "[ok] installed to $INSTALL_DIR/$INSTALLED_NAME"
echo

# --------------------------------------------------------------------------
# 4. Make sure ~/bin is on PATH, persistently
# --------------------------------------------------------------------------
# Pick the right rc file for the user's shell
CURRENT_SHELL="$(basename "${SHELL:-bash}")"
case "$CURRENT_SHELL" in
    zsh)  RC_FILE="$HOME/.zshrc" ;;
    bash) RC_FILE="$HOME/.bashrc" ;;
    *)    RC_FILE="$HOME/.bashrc" ;;  # sensible default
esac

PATH_LINE='export PATH="$HOME/bin:$PATH"'

if [[ -f "$RC_FILE" ]] && grep -Fxq "$PATH_LINE" "$RC_FILE"; then
    echo "[ok] PATH entry already present in $RC_FILE"
else
    {
        echo ''
        echo '# Added by rename-paper installer'
        echo "$PATH_LINE"
    } >> "$RC_FILE"
    echo "[ok] added ~/bin to PATH in $RC_FILE"
fi
echo

# --------------------------------------------------------------------------
# 5. Activate immediately in this shell (note: only affects a script run
#    with `source ./install.sh`; a plain `./install.sh` runs in a subshell
#    and cannot modify your interactive shell's environment)
# --------------------------------------------------------------------------
export PATH="$HOME/bin:$PATH"

echo "== Install complete =="
echo
if command -v rename-paper >/dev/null 2>&1; then
    echo "'rename-paper' is ready to use in this terminal session."
else
    echo "IMPORTANT: to use 'rename-paper' in THIS terminal right now, run:"
    echo "  source $RC_FILE"
    echo "New terminals will have it automatically."
fi
echo
echo "Try it:"
echo "  rename-paper --dry-run --email you@example.com somefile.pdf"
