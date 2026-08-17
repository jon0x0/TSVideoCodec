"""Terminal-aware single-line progress reporting."""

from __future__ import annotations

import sys

_active = False
_width = 0


def progress(message: str) -> None:
    """Replace the current terminal line, or emit a normal line when redirected."""
    global _active, _width
    if sys.stdout.isatty():
        width = max(_width, len(message))
        print(f"\r{message:<{width}}", end="", flush=True)
        _active = True
        _width = len(message)
    else:
        print(message, flush=True)


def progress_done(message: str | None = None) -> None:
    """Finish an interactive progress line and optionally replace it with a summary."""
    global _active, _width
    if sys.stdout.isatty():
        if message is not None:
            width = max(_width, len(message))
            print(f"\r{message:<{width}}", flush=True)
        elif _active:
            print(flush=True)
    elif message is not None:
        print(message, flush=True)
    _active = False
    _width = 0
