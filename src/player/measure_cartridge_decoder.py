#!/usr/bin/env python3
"""Measure every cartridge frame decode with Fuse frame/T-state counters."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

DEFAULT_FUSE = Path(os.environ.get("FUSE", shutil.which("fuse") or "fuse"))
HEX_LINE = re.compile(r"^0x([0-9a-f]+)$", re.IGNORECASE)
FRAME_TSTATES = 58_688
CPU_HZ = 3_528_000


def symbols(path: Path) -> dict[str, int]:
    return {name: int(value, 16) for name, value in re.findall(
        r"^([A-Z_]+)\s+EQU\s+0([0-9A-F]+)H$", path.read_text(), re.MULTILINE)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("build", type=Path)
    parser.add_argument("--fuse", type=Path, default=DEFAULT_FUSE)
    args = parser.parse_args()
    manifest = json.loads((args.build / "manifest.json").read_text())
    count = manifest["frame_count"]
    addresses = symbols(args.build / "cartridge_boot.symbols")
    start, ready, stop = (addresses[name] for name in ("NEXT_FRAME", "FRAME_READY", "PAUSE_LAST"))
    debugger = "\n".join((
        f"breakpoint 0x{start:04x}", f"breakpoint 0x{ready:04x}", f"breakpoint 0x{stop:04x}",
        "commands 1", "print spectrum:frames", "print ula:tstates", "continue", "end",
        "commands 2", "print spectrum:frames", "print ula:tstates", "continue", "end",
        "commands 3", "exit", "end"))
    startupinfo = subprocess.STARTUPINFO(); startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    result = subprocess.run([
        str(args.fuse), "--machine", "ts2068", "--speed", "5000", "--no-sound",
        "--dock", str((args.build / "svd_video_64k.dck").resolve()),
        "--debugger-command", debugger], capture_output=True, text=True, timeout=90,
        startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW)
    values = [int(m.group(1), 16) for line in result.stdout.splitlines()
              if (m := HEX_LINE.match(line.strip()))]
    if result.returncode or len(values) != count * 4:
        raise SystemExit(result.stderr or f"expected {count * 4} values, got {len(values)}")
    rows = []
    for index in range(count):
        sf, st, ef, et = values[index * 4:index * 4 + 4]
        elapsed = (ef - sf) * FRAME_TSTATES + et - st
        rows.append({"frame": index, "tstates": elapsed,
                     "milliseconds": round(elapsed * 1000 / CPU_HZ, 3)})
    (args.build / "decoder_timing.json").write_text(json.dumps(rows, indent=2) + "\n")
    deltas = rows[1:]
    print(f"keyframe: {rows[0]['tstates']} T-states ({rows[0]['milliseconds']:.3f} ms)")
    print(f"delta range: {min(r['tstates'] for r in deltas)}..{max(r['tstates'] for r in deltas)} T-states")
    print(f"delta mean: {sum(r['tstates'] for r in deltas) / len(deltas):.1f} T-states")
    print(f"wrote {args.build / 'decoder_timing.json'}")


if __name__ == "__main__":
    main()
