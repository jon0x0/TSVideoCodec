#!/usr/bin/env python3
"""Report source and encoded cell state for a generated ECM sequence region."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "encoder"))
from svd_ecm import ECMFrame, attribute_colours, screen_offset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--xb", type=int, required=True)
    parser.add_argument("--height", type=int, default=8)
    parser.add_argument("--width", type=int, default=4)
    args = parser.parse_args()
    prefixes = sorted(args.sequence.glob("frame_*.pix"))
    base_path = args.sequence / "auto_base.cells"
    frame_path = args.sequence / "auto_frame.cells"
    base = np.frombuffer(base_path.read_bytes(), dtype=np.uint8).reshape(192, 32) if base_path.exists() else None
    active = (np.frombuffer(frame_path.read_bytes(), dtype=np.uint8).reshape(len(prefixes), 192, 32)
              if frame_path.exists() else None)
    for frame_index, prefix in enumerate(prefixes):
        frame = ECMFrame(prefix.read_bytes(), prefix.with_suffix(".atr").read_bytes())
        source_path = prefix.with_name(prefix.stem + "_source.png")
        source = np.asarray(Image.open(source_path).convert("RGB")) if source_path.exists() else None
        records = []
        for y in range(args.y, args.y + args.height):
            for xb in range(args.xb, args.xb + args.width):
                offset = screen_offset(y, xb)
                bitmap = frame.bitmap[offset]; attribute = frame.attributes[offset]
                paper, ink = attribute_colours(attribute)
                source_mean = tuple(np.mean(source[y, xb * 8:xb * 8 + 8], axis=0).round().astype(int)) if source is not None else ()
                if bitmap or attribute:
                    decision = (f" base{int(base[y, xb])}/active{int(active[frame_index, y, xb])}"
                                if base is not None and active is not None else "")
                    records.append(f"{y}:{xb}={bitmap:02x}/{attribute:02x}({paper},{ink}){decision} src{source_mean}")
        print(prefix.stem + " " + " ".join(records))


if __name__ == "__main__":
    main()
