#!/usr/bin/env python3
"""Force the TAP exit path in Fuse and verify normal-video ROM state."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

from validate_ram_demo import DEFAULT_FUSE, symbol_address

HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()
    symbols = args.build / "svd_video_tap.symbols"
    pause = symbol_address(symbols, "PAUSE_LAST")
    exit_player = symbol_address(symbols, "EXIT_PLAYER")
    exit_return = symbol_address(symbols, "EXIT_STACK_RESTORED")
    original_sp = symbol_address(symbols, "ORIGINAL_SP")
    debugger = "\n".join((
        f"breakpoint 0x{pause:04x}", f"breakpoint 0x{exit_return:04x}",
        "commands 1", f"set z80:pc 0x{exit_player:04x}", "continue", "end",
        "commands 2", "print [0x5cc2]", "print z80:sp",
        f"print [0x{original_sp:04x}]", f"print [0x{original_sp + 1:04x}]", "exit", "end",
    ))
    startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run([
        str(args.fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--no-loading-sound", "--tape", str((args.build / "svd_video.tap").resolve()),
        "--auto-load", "--debugger-command", debugger,
    ], capture_output=True, text=True, timeout=90, startupinfo=startupinfo,
       creationflags=subprocess.CREATE_NO_WINDOW)
    values = [int(match.group(1), 16) for line in result.stdout.splitlines()
              if (match := HEX_LINE.match(line.strip()))]
    if result.returncode or len(values) != 4:
        raise SystemExit(result.stderr or f"unexpected Fuse exit values: {values}")
    if values[0] != 0:
        raise SystemExit(f"VIDMOD remained nonzero after exit: {values[0]}")
    saved_sp = values[2] | (values[3] << 8)
    print(f"exit mode and full workspace restoration reached ${exit_return:04X}")
    print(f"VIDMOD restored to normal; private playback SP was ${values[1]:04X}")
    print(f"saved BASIC SP is ${saved_sp:04X}")


if __name__ == "__main__":
    main()
