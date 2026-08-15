#!/usr/bin/env python3
"""Encode an image to raw TS2068 ECM planes and an exact preview."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from svd_ecm import encode_image


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output_prefix", type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--chroma-weight", type=float, default=1.0)
    group.add_argument("--adaptive-chroma", action="store_true")
    args = parser.parse_args()
    frame = encode_image(Image.open(args.input), chroma_weight=None if args.adaptive_chroma else args.chroma_weight)
    frame.write(args.output_prefix)
    preview = args.output_prefix.with_name(args.output_prefix.name + "_preview.png")
    frame.render().save(preview)
    print(f"wrote {args.output_prefix.with_suffix('.pix')} (6144 bytes)")
    print(f"wrote {args.output_prefix.with_suffix('.atr')} (6144 bytes)")
    print(f"wrote {preview}")


if __name__ == "__main__":
    main()
