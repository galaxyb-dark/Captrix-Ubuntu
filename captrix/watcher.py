"""Read the Google Live Caption bubble through the accessibility bus."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi

from captrix.stitch import Stitcher, normalize

BUTTON_ROLES = {"push button", "toggle button", "button", "menu item", "check box"}
STRUCTURAL_ROLES = {
    "panel",
    "dialog",
    "alert",
    "frame",
    "window",
    "filler",
    "grouping",
    "section",
    "landmark",
    "scroll pane",
    "viewport",
    "document frame",
    "document web",
    "document",
    "heading",
}
UI_NAMES = {
    "live caption",
    "translating",
    "back to tab",
    "turn off live caption for now",
    "show more lines",
    "show fewer lines",
    "pin",
    "unpin",
    "close",
    "caption settings",
    "settings",
    "minimize",
    "maximize",
    "restore",
    "expand",
    "collapse",
}
STATUS_PREFIXES = (
    "live caption is not available",
    "live caption is off",
    "live caption encountered",
    "live caption isn't available",
    "live caption isn’t available",
    "downloading ",
    "no audio",
    "waiting for audio",
)


def ensure_bus_address() -> None:
    """Point libatspi at the session accessibility bus when it is not set."""
    if os.environ.get("AT_SPI_BUS_ADDRESS"):
        return
    try:
        raw = subprocess.check_output(
            [
                "busctl",
                "--user",
                "call",
                "org.a11y.Bus",
                "/org/a11y/bus",
                "org.a11y.Bus",
                "GetAddress",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return
    if raw.startswith("s "):
        raw = raw[2:].strip().strip('"')
    if raw:
        os.environ["AT_SPI_BUS_ADDRESS"] = raw


def init_atspi() -> None:
    ensure_bus_address()
    # A dead flatpak app on the bus otherwise blocks the first lookup for ~15s.
    Atspi.set_timeout(1000, 200)
    Atspi.init()


def _is_browser(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("google chrome") or lowered in {
        "chromium",
        "chromium web browser",
        "brave",
        "brave web browser",
        "microsoft edge",
    }


def _squish(text: str) -> str:
    return " ".join(text.lower().split())


def is_status_message(text: str) -> bool:
    lowered = _squish(text)
    return any(lowered.startswith(prefix) for prefix in STATUS_PREFIXES)


def is_chrome_ui(text: str) -> bool:
    lowered = _squish(text)
    if lowered in UI_NAMES:
        return True
    if lowered.startswith("turn off live caption"):
        return True
    if lowered.startswith("show ") and lowered.endswith(" lines"):
        return True
    if "window switcher" in lowered or lowered.startswith("live caption visible"):
        return True
    return False


def _read_label(node, role: str) -> str:
    try:
        name = (node.get_name() or "").strip()
    except Exception:
        name = ""
    if name or role not in {"static", "text", "paragraph", "label"}:
        return name
    try:
        count = Atspi.Text.get_character_count(node)
        if count and count > 0:
            return Atspi.Text.get_text(node, 0, min(int(count), 5000)).strip()
    except Exception:
        return name
    return name


def collect_bubble(node, depth: int = 0) -> tuple[list[str], list[str]]:
    """Return caption lines and Chrome status lines from a Live Caption node."""
    if node is None or depth > 14:
        return [], []
    try:
        role = (node.get_role_name() or "").lower()
    except Exception:
        return [], []
    if role in BUTTON_ROLES or role == "notification":
        return [], []

    # The document name is the whole caption. Its children are just wrapped lines.
    if role in {"document frame", "document"}:
        label = _read_label(node, role)
        if (
            label
            and len(label) <= 8000
            and not is_status_message(label)
            and not is_chrome_ui(label)
        ):
            return [label], []

    captions: list[str] = []
    notices: list[str] = []
    try:
        child_count = node.get_child_count()
    except Exception:
        child_count = 0
    for index in range(child_count):
        try:
            child = node.get_child_at_index(index)
        except Exception:
            continue
        child_captions, child_notices = collect_bubble(child, depth + 1)
        captions.extend(child_captions)
        notices.extend(child_notices)
    if captions or notices:
        return captions, notices

    label = _read_label(node, role)
    if not label or len(label) > 800:
        return [], []
    if is_status_message(label):
        return [], [label]
    if is_chrome_ui(label):
        return [], []
    if role in STRUCTURAL_ROLES:
        return [], []
    return [label], []


class _AppGone(Exception):
    """The browser left the accessibility bus."""


@dataclass
class Reading:
    phase: str
    transcript: str
    live_text: str
    notice: str
    browser_found: bool
    bubble_open: bool


class CaptionWatcher:
    """Poll Chrome for the Live Caption bubble and stitch it into a transcript."""

    def __init__(self) -> None:
        self.stitcher = Stitcher()
        self._desktop = None
        self._last_live = ""
        self._suppress = ""
        self._next_wake = 0.0
        self._browsers: list = []
        self._next_scan = 0.0

    @property
    def transcript(self) -> str:
        return self.stitcher.transcript

    def clear(self) -> None:
        # Keep the sentence still sitting in the bubble from immediately refilling the panel.
        self._suppress = self._last_live
        self.stitcher.reset()

    def poll(self) -> Reading:
        browser, bubble = self._find_bubble()
        if browser is None:
            return Reading("no-browser", self.transcript, "", "", False, False)
        if bubble is None:
            return Reading("waiting", self.transcript, "", "", True, False)

        captions, notices = collect_bubble(bubble)
        if captions:
            notices = []
        live = normalize(" ".join(captions))
        notice = notices[-1] if notices else ""
        self._last_live = live
        if live and live != self._suppress:
            self._suppress = ""
            self.stitcher.update(live)
        if live:
            phase = "capturing"
        elif notice:
            phase = "notice"
        else:
            phase = "waiting"
        return Reading(phase, self.transcript, live, notice, True, True)

    def _browser_list(self):
        if self._browsers:
            return self._browsers
        now = time.monotonic()
        if now < self._next_scan:
            return []
        self._next_scan = now + 3.0
        if self._desktop is None:
            try:
                self._desktop = Atspi.get_desktop(0)
            except Exception:
                return []
        try:
            count = self._desktop.get_child_count()
        except Exception:
            self._desktop = None
            return []
        found = []
        for index in range(count):
            try:
                app = self._desktop.get_child_at_index(index)
            except Exception:
                continue
            if app is None:
                continue
            try:
                name = app.get_name() or ""
            except Exception:
                continue
            if _is_browser(name):
                found.append(app)
        self._browsers = found
        return found

    def _find_bubble(self):
        browsers = self._browser_list()
        if not browsers:
            return None, None
        now = time.monotonic()
        wake = now >= self._next_wake
        if wake:
            self._next_wake = now + 2.0
        found_browser = browsers[0]
        survivors = []
        for index, app in enumerate(browsers):
            try:
                bubble = self._bubble_in(app, wake)
            except _AppGone:
                continue
            survivors.append(app)
            if bubble is not None:
                survivors.extend(browsers[index + 1 :])
                self._browsers = survivors
                return app, bubble
        self._browsers = survivors
        if not survivors:
            return None, None
        return found_browser if found_browser in survivors else survivors[0], None

    def _bubble_in(self, app, wake: bool):
        try:
            count = app.get_child_count()
        except Exception as exc:
            raise _AppGone from exc
        candidates = []
        for index in range(count):
            try:
                frame = app.get_child_at_index(index)
            except Exception:
                continue
            if frame is None:
                continue
            if wake:
                try:
                    frame.get_attributes()
                except Exception:
                    pass
            try:
                name = frame.get_name() or ""
            except Exception:
                continue
            if "live caption" in name.lower():
                candidates.append(frame)
        if not candidates:
            return None
        # Chrome keeps a leftover bubble that only says captions are unavailable.
        # Prefer the one that currently has caption text.
        best = None
        best_length = -1
        for frame in candidates:
            captions, _notices = collect_bubble(frame)
            length = len(normalize(" ".join(captions)))
            if length > best_length:
                best = frame
                best_length = length
        if best is not None and best_length > 0:
            return best
        return candidates[-1]
