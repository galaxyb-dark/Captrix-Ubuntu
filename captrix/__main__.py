"""Run Captrix: python3 -m captrix"""

from __future__ import annotations

import argparse
import sys

from captrix.watcher import CaptionWatcher, init_atspi
from captrix.window import run_window


def _print_once() -> int:
    init_atspi()
    reading = CaptionWatcher().poll()
    print(f"phase: {reading.phase}")
    print(f"browser: {reading.browser_found}")
    print(f"bubble: {reading.bubble_open}")
    print(f"notice: {reading.notice}")
    print(f"live: {reading.live_text}")
    print(f"transcript: {reading.transcript}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Catch Google Live Caption text into a window you can copy from."
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Print one reading from the Live Caption bubble and exit.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Paste the selected caption into the focused field and press Enter.",
    )
    args = parser.parse_args(argv)
    if args.once:
        return _print_once()
    return run_window()


if __name__ == "__main__":
    sys.exit(main())
