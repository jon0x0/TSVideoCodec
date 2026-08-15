#!/usr/bin/env python3
"""Measure raw and PackBits storage for encoded ECM keyframes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "encoder"))
from keyframe_codec import decode_packbits, encode_packbits


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", nargs="*", type=Path)
    parser.add_argument("--tap", type=Path,
                        help="optionally check whether each raw keyframe occurs in a TAP")
    parser.add_argument("--tap-key", type=Path,
                        help="measure raw keyframe candidates referenced inside a TAP image")
    args = parser.parse_args()
    for sequence in args.sequence:
        bitmap = (sequence / "frame_00000.pix").read_bytes()
        attributes = (sequence / "frame_00000.atr").read_bytes()
        if len(bitmap) != 0x1800 or len(attributes) != 0x1800:
            raise SystemExit(f"{sequence}: expected two 6144-byte ECM planes")
        packed_bitmap = encode_packbits(bitmap)
        packed_attributes = encode_packbits(attributes)
        if (decode_packbits(packed_bitmap, len(bitmap)) != bitmap or
                decode_packbits(packed_attributes, len(attributes)) != attributes):
            raise SystemExit(f"{sequence}: compression round trip failed")
        packed = len(packed_bitmap) + len(packed_attributes)
        saved = len(bitmap) + len(attributes) - packed
        print(f"{sequence}: bitmap={len(packed_bitmap)}, attributes={len(packed_attributes)}, "
              f"total={packed}, saved={saved} ({saved / 12288:.1%})")
        if args.tap:
            location = args.tap.read_bytes().find(bitmap + attributes)
            print(f"  raw keyframe in {args.tap}: " +
                  (f"yes, file offset {location}" if location >= 0 else "no"))
    if args.tap_key:
        data = args.tap_key.read_bytes()
        blocks = []
        offset = 0
        while offset < len(data):
            size = int.from_bytes(data[offset:offset + 2], "little")
            blocks.append(data[offset + 2:offset + 2 + size])
            offset += 2 + size
        code = blocks[-1][1:-1]
        load_address = 0x7800
        candidates = []
        for pos in range(len(code) - 2):
            if code[pos] != 1:
                continue
            address = int.from_bytes(code[pos + 1:pos + 3], "little")
            key_offset = address - load_address
            if pos < key_offset and 0 <= key_offset <= len(code) - 0x3000:
                key = code[key_offset:key_offset + 0x3000]
                bitmap_size = len(encode_packbits(key[:0x1800]))
                attribute_size = len(encode_packbits(key[0x1800:]))
                candidates.append((pos, address, bitmap_size, attribute_size))
        for pos, address, bitmap_size, attribute_size in candidates:
            packed = bitmap_size + attribute_size
            print(f"{args.tap_key}: table-offset={pos}, key-address=${address:04X}, "
                  f"bitmap={bitmap_size}, attributes={attribute_size}, total={packed}, "
                  f"saved={0x3000 - packed} ({(0x3000-packed)/0x3000:.1%})")


if __name__ == "__main__":
    main()
