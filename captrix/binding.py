"""The paste shortcut, and the press-to-record behavior for changing it."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from captrix.uinput import BTN_EXTRA, BTN_LEFT, BTN_MIDDLE, BTN_RIGHT, BTN_SIDE

KEY_ESC = 1
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_LEFTALT = 56
KEY_RIGHTSHIFT = 54
KEY_RIGHTCTRL = 97
KEY_RIGHTALT = 100
KEY_LEFTMETA = 125
KEY_RIGHTMETA = 126

# Right-side modifiers count the same as the left-side ones.
CANON = {
    KEY_RIGHTCTRL: KEY_LEFTCTRL,
    KEY_RIGHTSHIFT: KEY_LEFTSHIFT,
    KEY_RIGHTALT: KEY_LEFTALT,
    KEY_RIGHTMETA: KEY_LEFTMETA,
}

MODIFIERS = frozenset(CANON) | frozenset(CANON.values())

MODIFIER_LABELS = (
    (frozenset({KEY_LEFTCTRL, KEY_RIGHTCTRL}), "Ctrl"),
    (frozenset({KEY_LEFTSHIFT, KEY_RIGHTSHIFT}), "Shift"),
    (frozenset({KEY_LEFTALT, KEY_RIGHTALT}), "Alt"),
    (frozenset({KEY_LEFTMETA, KEY_RIGHTMETA}), "Super"),
)

_BUTTONS = {
    BTN_LEFT: "Left click",
    BTN_RIGHT: "Right click",
    BTN_MIDDLE: "Middle click",
    BTN_SIDE: "Mouse 4",
    BTN_EXTRA: "Mouse 5",
    0x115: "Mouse 6",
    0x116: "Mouse 7",
}

_LETTERS = {
    16: "Q",
    17: "W",
    18: "E",
    19: "R",
    20: "T",
    21: "Y",
    22: "U",
    23: "I",
    24: "O",
    25: "P",
    30: "A",
    31: "S",
    32: "D",
    33: "F",
    34: "G",
    35: "H",
    36: "J",
    37: "K",
    38: "L",
    44: "Z",
    45: "X",
    46: "C",
    47: "V",
    48: "B",
    49: "N",
    50: "M",
}

_DIGITS = {code: str((code - 1) % 10) for code in range(2, 12)}
_FUNCTIONS = {59 + index: f"F{index + 1}" for index in range(10)}
_FUNCTIONS[87] = "F11"
_FUNCTIONS[88] = "F12"

_NAMED = {
    1: "Esc",
    12: "-",
    13: "=",
    14: "Backspace",
    15: "Tab",
    26: "[",
    27: "]",
    28: "Enter",
    39: ";",
    40: "'",
    41: "`",
    43: "\\",
    51: ",",
    52: ".",
    53: "/",
    57: "Space",
    102: "Home",
    103: "Up",
    104: "Page Up",
    105: "Left",
    106: "Right",
    107: "End",
    108: "Down",
    109: "Page Down",
    110: "Insert",
    111: "Delete",
}


def canonicalize(keys: frozenset[int]) -> frozenset[int]:
    return frozenset(CANON.get(key, key) for key in keys)


def key_name(code: int) -> str:
    code = CANON.get(code, code)
    for group, name in MODIFIER_LABELS:
        if code in group:
            return name
    if code in _BUTTONS:
        return _BUTTONS[code]
    if code in _LETTERS:
        return _LETTERS[code]
    if code in _DIGITS:
        return _DIGITS[code]
    if code in _FUNCTIONS:
        return _FUNCTIONS[code]
    if code in _NAMED:
        return _NAMED[code]
    return f"Key {code}"


def uses_left_button(keys: frozenset[int]) -> bool:
    return BTN_LEFT in keys


def label_for(keys: frozenset[int]) -> str:
    if not keys:
        return "Not set"
    if keys == frozenset({BTN_LEFT, BTN_RIGHT}):
        return "Left + Right click"
    parts: list[str] = []
    rest = set(keys)
    for group, name in MODIFIER_LABELS:
        if rest & group:
            parts.append(name)
            rest -= group
    parts.extend(key_name(code) for code in sorted(rest))
    return " + ".join(parts)


@dataclass(frozen=True)
class Shortcut:
    keys: frozenset[int]

    @classmethod
    def default(cls) -> Shortcut:
        # F8. A mouse chord was holding clicks back and light selections never landed.
        return cls(frozenset({66}))

    @property
    def label(self) -> str:
        return label_for(self.keys)

    @property
    def dismiss_menu(self) -> bool:
        return BTN_RIGHT in self.keys


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "captrix" / "shortcut.json"


def load_shortcut() -> Shortcut:
    try:
        data = json.loads(config_path().read_text())
        keys = canonicalize(frozenset(int(key) for key in data["keys"]))
    except (OSError, ValueError, KeyError, TypeError):
        return Shortcut.default()
    if not keys or keys <= MODIFIERS or uses_left_button(keys):
        return Shortcut.default()
    return Shortcut(keys)


def save_shortcut(shortcut: Shortcut) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"keys": sorted(shortcut.keys)}) + "\n")
    temporary.replace(path)


class Matcher:
    """Fire once each time the shortcut is pressed, not again until it is released."""

    def __init__(self, binding: frozenset[int]) -> None:
        self.binding = binding
        self.held: set[int] = set()
        self.latched = False

    def event(self, code: int, value: int) -> bool:
        if value == 2:
            return False
        if value == 1:
            self.held.add(code)
        elif value == 0:
            self.held.discard(code)
        else:
            return False
        if canonicalize(frozenset(self.held)) != self.binding:
            self.latched = False
            return False
        if value == 1 and not self.latched:
            self.latched = True
            return True
        return False


class Capture:
    """Record the next shortcut the moment its key goes down. Esc cancels."""

    def __init__(self, started: float, seed: set[int] | None = None) -> None:
        self.started = started
        self.held: set[int] = {code for code in (seed or ()) if code in MODIFIERS}

    def event(self, code: int, value: int, now: float) -> str | frozenset[int] | None:
        if value == 2 or code == BTN_LEFT:
            # Left click selects text and presses the panel buttons. It is never a shortcut.
            return None
        # The click that opened the panel is not the shortcut.
        if now - self.started < 0.25:
            if code in MODIFIERS:
                if value == 1:
                    self.held.add(code)
                elif value == 0:
                    self.held.discard(code)
            return None
        if value == 0:
            self.held.discard(code)
            return None
        if value != 1:
            return None
        if code == KEY_ESC and not (self.held - {KEY_ESC}):
            return "cancel"
        self.held.add(code)
        if code in MODIFIERS:
            return None
        keys = canonicalize(frozenset(self.held))
        if not keys or keys <= MODIFIERS:
            return None
        return keys
