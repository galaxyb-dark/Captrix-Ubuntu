"""Virtual keyboard used to paste into whichever window is focused."""

from __future__ import annotations

import fcntl
import os
import struct
import threading
import time

EV_SYN = 0x00
EV_KEY = 0x01
EV_REL = 0x02
EV_MSC = 0x04
EV_REP = 0x14
SYN_REPORT = 0

KEY_ESC = 1
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_V = 47

BTN_LEFT = 0x110
BTN_RIGHT = 0x111
BTN_MIDDLE = 0x112
BTN_SIDE = 0x113
BTN_EXTRA = 0x114

EVENT_SIZE = struct.calcsize("llHHi")


def _ioc(direction: int, type_: int, number: int, size: int = 0) -> int:
    return (direction << 30) | (size << 16) | (type_ << 8) | number


def _IO(type_: str, number: int) -> int:
    return _ioc(0, ord(type_), number)


def _IOW(type_: str, number: int, size: int) -> int:
    return _ioc(1, ord(type_), number, size)


def _IOR(type_: str, number: int, size: int) -> int:
    return _ioc(2, ord(type_), number, size)


UI_SET_EVBIT = _IOW("U", 100, 4)
UI_SET_KEYBIT = _IOW("U", 101, 4)
UI_SET_RELBIT = _IOW("U", 102, 4)
UI_SET_MSCBIT = _IOW("U", 104, 4)
UI_DEV_CREATE = _IO("U", 1)
UI_DEV_DESTROY = _IO("U", 2)
EVIOCGRAB = _IOW("E", 0x90, 4)


def device_name(fd: int) -> str:
    raw = bytearray(256)
    fcntl.ioctl(fd, _IOR("E", 0x06, len(raw)), raw)
    return raw.split(b"\x00", 1)[0].decode(errors="replace")


def bit_is_set(fd: int, event_type: int, code: int) -> bool:
    length = max(64, code // 8 + 1)
    raw = bytearray(length)
    fcntl.ioctl(fd, _IOR("E", 0x20 + event_type, length), raw)
    return bool(raw[code // 8] & (1 << (code % 8)))


def pack_event(type_: int, code: int, value: int) -> bytes:
    now = time.time()
    second = int(now)
    microsecond = int((now - second) * 1_000_000)
    return struct.pack("llHHi", second, microsecond, type_, code, value)


class VirtualKeyboard:
    """One keyboard for the life of the app. The first keys after creation can be lost."""

    def __init__(self) -> None:
        self.fd = os.open("/dev/uinput", os.O_WRONLY)
        self._lock = threading.Lock()
        try:
            self._setup()
        except Exception:
            os.close(self.fd)
            raise

    def _setup(self) -> None:
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_KEY)
        # Without key repeat, udev labels this as a button pad and the desktop
        # drops Ctrl+V instead of typing it.
        fcntl.ioctl(self.fd, UI_SET_EVBIT, EV_REP)
        for code in range(1, 128):
            fcntl.ioctl(self.fd, UI_SET_KEYBIT, code)
        name = b"Captrix keyboard"
        setup = struct.pack("HHHH80sI", 0x06, 0x01, 0x01, 1, name, 0)
        fcntl.ioctl(self.fd, _IOW("U", 3, len(setup)), setup)
        fcntl.ioctl(self.fd, UI_DEV_CREATE)
        # The desktop ignores the first keys if they arrive before it sees this device.
        time.sleep(0.35)

    def emit(self, type_: int, code: int, value: int) -> None:
        os.write(self.fd, pack_event(type_, code, value))

    def tap(self, code: int) -> None:
        with self._lock:
            self.emit(EV_KEY, code, 1)
            self.emit(EV_SYN, SYN_REPORT, 0)
            time.sleep(0.02)
            self.emit(EV_KEY, code, 0)
            self.emit(EV_SYN, SYN_REPORT, 0)

    def press_ctrl_v(self) -> None:
        with self._lock:
            self.emit(EV_KEY, KEY_LEFTCTRL, 1)
            self.emit(EV_KEY, KEY_V, 1)
            self.emit(EV_SYN, SYN_REPORT, 0)
            time.sleep(0.03)
            self.emit(EV_KEY, KEY_V, 0)
            self.emit(EV_KEY, KEY_LEFTCTRL, 0)
            self.emit(EV_SYN, SYN_REPORT, 0)

    def close(self) -> None:
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)


_keyboard: VirtualKeyboard | None = None
_keyboard_error: str | None = None


def prepare_keyboard() -> str | None:
    """Open the virtual keyboard once. Return an error message, if it cannot be opened."""
    global _keyboard, _keyboard_error
    if _keyboard is not None:
        return None
    if _keyboard_error is not None:
        return _keyboard_error
    try:
        _keyboard = VirtualKeyboard()
    except OSError as error:
        _keyboard_error = (
            "Captrix can't type into the other window. "
            f"Join the input group, then log out and back in. ({error.strerror})"
        )
        return _keyboard_error
    return None


def keyboard() -> VirtualKeyboard | None:
    prepare_keyboard()
    return _keyboard


def close_keyboard() -> None:
    global _keyboard
    if _keyboard is not None:
        _keyboard.close()
        _keyboard = None
