#!/usr/bin/env python3
"""Verify that Z80 decoder constants match the executable Python SVD spec."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "encoder"))

import svd_stream


def main() -> None:
    source = (ROOT / "player" / "svd_decoder.asm").read_text(encoding="utf-8")
    definitions = {
        name: int(value[1:], 16) if value.startswith("$") else int(value)
        for name, value in re.findall(r"^([A-Z_]+)\s+EQU\s+(\$[0-9A-Fa-f]+|[0-9]+)\s*$", source, re.MULTILINE)
    }
    expected = {
        "PLANE_SIZE": svd_stream.PLANE_SIZE,
        "FRAME_KEY": svd_stream.FRAME_KEY,
        "FRAME_DELTA": svd_stream.FRAME_DELTA,
        "FRAME_REPEAT": svd_stream.FRAME_REPEAT,
        "FRAME_SPARSE": svd_stream.FRAME_SPARSE,
        "CMD_END": svd_stream.CMD_END,
        "CMD_SKIP": svd_stream.CMD_SKIP,
        "CMD_BITMAP": svd_stream.CMD_BITMAP,
        "CMD_ATTRIBUTE": svd_stream.CMD_ATTRIBUTE,
        "CMD_BOTH": svd_stream.CMD_BOTH,
    }
    mismatches = [
        f"{name}: asm={definitions.get(name)!r}, python={value}"
        for name, value in expected.items()
        if definitions.get(name) != value
    ]
    if mismatches:
        raise SystemExit("decoder contract mismatch:\n" + "\n".join(mismatches))
    print(f"decoder contract verified ({len(expected)} constants)")


if __name__ == "__main__":
    main()
