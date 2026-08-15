#!/usr/bin/env python3
"""Assemble the SVD decoder reproducibly with the user's Pasmo 0.5.5 tree."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
from toolchain import assemble_pasmo


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pasmo", default=None,
                        help="Pasmo executable; defaults to PASMO or PATH")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "player" / "svd_decoder.bin")
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / "tools" / "validate_decoder_contract.py")], check=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    symbols = args.output.with_suffix(".symbols")
    sys.path.insert(0, str(ROOT / "encoder"))
    from svd_ecm import screen_offset
    rows_include = args.output.parent / "bitmap_rows.inc"
    rows_include.write_text(
        "\n".join(f"                DW      ${0x4000 + screen_offset(y, 0):04X}" for y in range(192)) + "\n",
        encoding="ascii",
    )
    assemble_pasmo(ROOT / "player" / "svd_decoder.asm", args.output, symbols,
                   args.output.parent, args.pasmo)
    if not args.output.read_bytes():
        raise SystemExit("Pasmo produced an empty decoder binary")
    print(f"assembled {args.output} ({args.output.stat().st_size} bytes)")
    print(f"wrote {symbols}")


if __name__ == "__main__":
    main()
