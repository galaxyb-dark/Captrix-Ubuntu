"""Listen for the paste shortcut without delaying ordinary clicks."""

from __future__ import annotations

import os
import select
import struct
import threading
import time
from pathlib import Path

from gi.repository import GLib

from captrix.binding import Capture, Matcher
from captrix.uinput import (
    BTN_RIGHT,
    EV_KEY,
    EVENT_SIZE,
    bit_is_set,
    device_name,
    prepare_keyboard,
)

EVENT_FORMAT = "llHHi"


class HotkeyWatch:
    """Call back when the saved shortcut is pressed, or when a new one is recorded."""

    def __init__(self, binding: frozenset[int], on_trigger, on_captured, on_cancel) -> None:
        self._on_trigger = on_trigger
        self._on_captured = on_captured
        self._on_cancel = on_cancel
        self._matcher = Matcher(binding)
        self._capture: Capture | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._error: str | None = None
        self._fds: list[int] = []
        self._buffers: dict[int, bytes] = {}
        self._wake_r, self._wake_w = os.pipe()

    def start(self) -> str | None:
        prepare_keyboard()
        self._thread = threading.Thread(target=self._run, name="captrix-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2)
        return self._error

    def stop(self) -> None:
        self._stop.set()
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass
        if self._thread is not None:
            self._thread.join(timeout=1.5)
        else:
            self._close_all()

    def set_binding(self, binding: frozenset[int]) -> None:
        with self._lock:
            self._matcher.binding = binding
            self._matcher.latched = False

    def begin_capture(self) -> None:
        with self._lock:
            self._capture = Capture(time.monotonic(), seed=set(self._matcher.held))
            self._matcher.latched = False

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture = None

    def _run(self) -> None:
        try:
            self._open_devices()
        except Exception as error:
            self._error = f"Captrix can't listen for a shortcut. {error}"
        self._ready.set()
        if self._error or not self._fds:
            return
        try:
            self._loop()
        finally:
            self._close_all()

    def _open_devices(self) -> None:
        for path in sorted(Path("/dev/input").glob("event*")):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                if device_name(fd).startswith("Captrix") or not bit_is_set(fd, 0, EV_KEY):
                    os.close(fd)
                    continue
            except OSError:
                os.close(fd)
                continue
            self._fds.append(fd)
            self._buffers[fd] = b""
        if not self._fds:
            self._error = (
                "Captrix can't see the keyboard or mouse. "
                "Join the input group, then log out and back in."
            )

    def _loop(self) -> None:
        while not self._stop.is_set():
            readable, _, _ = select.select(self._fds + [self._wake_r], [], [], 0.4)
            if self._wake_r in readable:
                try:
                    os.read(self._wake_r, 16)
                except OSError:
                    pass
                if self._stop.is_set():
                    return
            for fd in list(readable):
                if fd == self._wake_r or fd not in self._buffers:
                    continue
                try:
                    chunk = os.read(fd, EVENT_SIZE * 64)
                except BlockingIOError:
                    continue
                except OSError:
                    self._drop(fd)
                    continue
                if not chunk:
                    self._drop(fd)
                    continue
                self._buffers[fd] += chunk
                while len(self._buffers[fd]) >= EVENT_SIZE:
                    raw = self._buffers[fd][:EVENT_SIZE]
                    self._buffers[fd] = self._buffers[fd][EVENT_SIZE:]
                    _second, _micro, type_, code, value = struct.unpack(EVENT_FORMAT, raw)
                    if type_ == EV_KEY:
                        self._on_key(code, value)

    def _on_key(self, code: int, value: int) -> None:
        with self._lock:
            fired = self._matcher.event(code, value)
            if self._capture is not None:
                result = self._capture.event(code, value, time.monotonic())
                if result is None:
                    return
                self._capture = None
                outcome: object = result
            elif fired:
                outcome = ("trigger", BTN_RIGHT in self._matcher.binding)
            else:
                return
        if outcome == "cancel":
            GLib.idle_add(self._on_cancel)
        elif isinstance(outcome, frozenset):
            GLib.idle_add(self._on_captured, outcome)
        else:
            GLib.idle_add(self._on_trigger, outcome[1])

    def _close_all(self) -> None:
        for fd in list(self._fds):
            self._drop(fd)
        for name in ("_wake_r", "_wake_w"):
            pipe = getattr(self, name)
            if pipe < 0:
                continue
            setattr(self, name, -1)
            try:
                os.close(pipe)
            except OSError:
                pass

    def _drop(self, fd: int) -> None:
        self._buffers.pop(fd, None)
        if fd in self._fds:
            self._fds.remove(fd)
        try:
            os.close(fd)
        except OSError:
            pass
