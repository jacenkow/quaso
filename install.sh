#!/bin/sh
# Install quaso.
#
#   curl -fsSL https://raw.githubusercontent.com/jacenkow/quaso/main/install.sh | sh
#
# Prefers whatever you already use for Python command line tools, and
# falls back to a private virtualenv under ~/.local/share. Nothing is
# installed system-wide and nothing needs root.
#
# QUASO_REF      a tag, branch or commit to install (default: main)
# QUASO_INSTALLER  uv | pipx | venv, to force one (default: whichever
#                  of those is already present)
# QUASO_PREFIX   where the venv method installs (default: ~/.local).
#                uv and pipx keep their own locations and ignore it.

set -eu

REPO="https://github.com/jacenkow/quaso"
REF="${QUASO_REF:-main}"
INSTALLER="${QUASO_INSTALLER:-auto}"
PREFIX="${QUASO_PREFIX:-$HOME/.local}"
VENV="$PREFIX/share/quaso"
BIN="$PREFIX/bin"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

python_ok() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

find_python() {
    for candidate in python3.13 python3.12 python3.11 python3; do
        if have "$candidate" && python_ok "$candidate"; then
            printf '%s' "$candidate"
            return 0
        fi
    done
    return 1
}

say "Installing quaso from $REPO@$REF"

# uv and pipx both keep a tool in its own environment and put a launcher
# on PATH, which is exactly what is wanted here. Whichever is already
# present is the one that will be least surprising to uninstall later.
if [ "$INSTALLER" = auto ]; then
    if have uv; then INSTALLER=uv
    elif have pipx; then INSTALLER=pipx
    else INSTALLER=venv
    fi
fi

if [ "$INSTALLER" = uv ]; then
    have uv || die "QUASO_INSTALLER=uv but uv is not installed"
    say "  using uv"
    uv tool install --force "git+$REPO@$REF"
    INSTALLED_WITH="uv tool uninstall quaso"
elif [ "$INSTALLER" = pipx ]; then
    have pipx || die "QUASO_INSTALLER=pipx but pipx is not installed"
    say "  using pipx"
    pipx install --force "git+$REPO@$REF"
    INSTALLED_WITH="pipx uninstall quaso"
elif [ "$INSTALLER" = venv ]; then
    PYTHON=$(find_python) || die "quaso needs Python 3.11 or newer; none found"
    say "  using $PYTHON, into $VENV"
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet "git+$REPO@$REF"
    mkdir -p "$BIN"
    ln -sf "$VENV/bin/quaso" "$BIN/quaso"
    INSTALLED_WITH="rm -rf $VENV $BIN/quaso"
else
    die "unknown QUASO_INSTALLER '$INSTALLER' (use uv, pipx or venv)"
fi

say ""
if have quaso; then
    say "Installed: $(quaso --version)"
    say "Run 'quaso' in a project directory to start."
else
    say "Installed, but $BIN is not on your PATH. Add it:"
    say ""
    say "    export PATH=\"$BIN:\$PATH\""
    say ""
    say "then run 'quaso' in a project directory."
fi
say "To remove it later: $INSTALLED_WITH"
