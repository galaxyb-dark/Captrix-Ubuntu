"""Paste the selected caption into whichever field is focused, then press Enter."""

from __future__ import annotations

import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

from captrix.uinput import KEY_ENTER, KEY_ESC, keyboard

SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
KEY_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/captrix-send/"


def remove_send_shortcut() -> None:
    """Drop the old Ctrl+Alt+Enter binding and leave every other shortcut alone."""
    source = Gio.SettingsSchemaSource.get_default()
    if source is None or source.lookup(SCHEMA, True) is None:
        return
    settings = Gio.Settings.new(SCHEMA)
    current = [path for path in settings.get_strv("custom-keybindings") if path != KEY_PATH]
    if list(settings.get_strv("custom-keybindings")) != current:
        settings.set_strv("custom-keybindings", current)
        Gio.Settings.sync()


def paste_and_enter(text: str = "", dismiss_menu: bool = False) -> str | None:
    """Type Ctrl+V, then Enter, into the focused window. Return an error, if any."""
    board = keyboard()
    if board is None:
        return (
            "Captrix can't type into the other window. "
            "Join the input group, then log out and back in."
        )
    try:
        if dismiss_menu:
            board.tap(KEY_ESC)
            time.sleep(0.08)
        board.press_ctrl_v()
        # Let a long selection land before Enter submits it.
        time.sleep(min(0.8, 0.25 + len(text) / 8000))
        board.tap(KEY_ENTER)
    except OSError as error:
        return error.strerror or "Could not type into the focused field."
    return None
