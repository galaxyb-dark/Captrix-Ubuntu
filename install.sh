#!/usr/bin/env bash
# Install Captrix into the current user's app menu.
# Run this on each laptop, from the Captrix folder:
#   ./install.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
BIN_DIR="${HOME}/.local/bin"

set +e
python3 - << 'PY'
import sys

try:
    import gi
except ImportError:
    sys.exit(1)

try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Atspi", "2.0")
    from gi.repository import Adw, Atspi, Gtk  # noqa: F401
except ValueError:
    sys.exit(1)
PY
status=$?
set -e
if [[ "$status" -ne 0 ]]; then
  echo "Captrix needs a few Ubuntu packages before it can be installed."
  echo "Run this, then run ./install.sh again:"
  echo
  echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-atspi-2.0"
  exit 1
fi

chmod +x "$ROOT/bin/captrix"
mkdir -p "$DESKTOP_DIR" "$ICON_DIR" "$BIN_DIR"

cp "$ROOT/data/icons/com.captrix.Captrix.svg" "$ICON_DIR/com.captrix.Captrix.svg"
ln -sfn "$ROOT/bin/captrix" "$BIN_DIR/captrix"

python3 - "$ROOT" "$DESKTOP_DIR/com.captrix.Captrix.desktop" << 'PY'
import pathlib
import sys

root, destination = sys.argv[1:]
template = pathlib.Path(root, "com.captrix.Captrix.desktop").read_text()
pathlib.Path(destination).write_text(template.replace("@ROOT@", root))
PY

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f "${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Captrix is installed for this user."
echo "Open it from the app grid. If it does not appear, log out and back in."
echo "The captrix command is $BIN_DIR/captrix"
echo "If you move this folder, run ./install.sh again."
if ! id -nG "$USER" | grep -qw input; then
  echo
  echo "The paste shortcut needs the input group."
  echo "  sudo usermod -aG input \"$USER\""
  echo "Then log out and back in."
fi
