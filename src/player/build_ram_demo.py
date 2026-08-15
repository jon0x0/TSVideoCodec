#!/usr/bin/env python3
"""Build a reproducible three-frame SVD ECM autostart TAP demonstration."""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
from toolchain import assemble_pasmo
LOAD_ADDRESS = 0x7800
STACK_TOP = 0xFF00


def number(value: int) -> bytes:
    text = str(value).encode("ascii")
    return text + bytes((0x0E, 0, 0, value & 0xFF, value >> 8, 0))


def basic_line(line_number: int, body: bytes) -> bytes:
    content = body + b"\x0D"
    return line_number.to_bytes(2, "big") + len(content).to_bytes(2, "little") + content


def tap_block(flag: int, payload: bytes) -> bytes:
    body = bytes((flag,)) + payload
    checksum = 0
    for value in body:
        checksum ^= value
    framed = body + bytes((checksum,))
    return len(framed).to_bytes(2, "little") + framed


def tap_header(file_type: int, name: str, length: int, parameter1: int, parameter2: int) -> bytes:
    payload = bytes((file_type,)) + name[:10].ljust(10).encode("ascii")
    payload += length.to_bytes(2, "little") + parameter1.to_bytes(2, "little") + parameter2.to_bytes(2, "little")
    return tap_block(0, payload)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path, nargs="?")
    parser.add_argument("--sequence", type=Path, help="use an existing three-frame .pix/.atr sequence")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "ram_demo")
    parser.add_argument("--fps", type=int, default=12)
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--change-penalty", type=float, default=0)
    parser.add_argument("--source-gamma", type=float, default=0.8)
    parser.add_argument("--dither-strength", type=float, default=0.0)
    parser.add_argument("--geometry", choices=("fit", "crop"), default="crop")
    parser.add_argument("--edge-weight", type=float, default=0.0)
    parser.add_argument("--dither-mode", choices=("sierra-lite", "legacy"), default="sierra-lite")
    parser.add_argument("--brightness", type=float, default=-0.02)
    parser.add_argument("--contrast", type=float, default=1.0)
    parser.add_argument("--saturation", type=float, default=1.0)
    parser.add_argument("--sierra-gamma", type=float, default=1.3)
    parser.add_argument("--temporal-attr-penalty", type=float, default=0.01)
    parser.add_argument("--temporal-pixel-penalty", type=float, default=0.01)
    parser.add_argument("--delta-format", choices=("sparse", "runs"), default="runs")
    parser.add_argument("--pasmo", default=None,
                        help="Pasmo executable; defaults to PASMO or PATH")
    args = parser.parse_args()
    if not 1 <= args.frames <= 255:
        parser.error("--frames must be between 1 and 255")
    args.output.mkdir(parents=True, exist_ok=True)
    sequence = args.sequence or (args.output / "sequence")
    if args.sequence is None:
        if args.video is None:
            parser.error("video is required unless --sequence is supplied")
        subprocess.run([
            sys.executable, str(ROOT / "encoder" / "encode_sequence.py"), str(args.video), str(sequence),
            "--fps", str(args.fps), "--max-frames", str(args.frames), "--change-penalty", str(args.change_penalty),
            "--start-seconds", str(args.start_seconds),
            "--source-gamma", str(args.source_gamma), "--dither-strength", str(args.dither_strength),
            "--geometry", args.geometry,
            "--edge-weight", str(args.edge_weight),
            "--dither-mode", args.dither_mode,
            "--brightness", str(args.brightness), "--contrast", str(args.contrast),
            "--saturation", str(args.saturation), "--sierra-gamma", str(args.sierra_gamma),
            "--temporal-attr-penalty", str(args.temporal_attr_penalty),
            "--temporal-pixel-penalty", str(args.temporal_pixel_penalty),
        ], check=True)
    stream = args.output / "demo.svd"
    subprocess.run([
        sys.executable, str(ROOT / "encoder" / "pack_svd.py"), str(sequence), str(stream),
        "--fps-num", str(args.fps), "--delta-format", args.delta_format,
    ], check=True)

    sys.path.insert(0, str(ROOT / "encoder"))
    from svd_ecm import screen_offset
    (args.output / "bitmap_rows.inc").write_text(
        "\n".join(f"                DW      ${0x4000 + screen_offset(y, 0):04X}" for y in range(192)) + "\n",
        encoding="ascii",
    )
    (args.output / "demo_config.inc").write_text(
        f"FRAME_COUNT     EQU     {args.frames}\n", encoding="ascii"
    )
    binary = args.output / "svd_ram_demo.bin"
    symbols = args.output / "svd_ram_demo.symbols"
    assemble_pasmo(ROOT / "player" / "svd_ram_demo.asm", binary, symbols,
                   [args.output, ROOT / "player"], args.pasmo)

    load_address = LOAD_ADDRESS
    basic = b"".join((
        basic_line(10, bytes((0xFD, 0x20)) + number(load_address - 1)),
        basic_line(20, bytes((0xEF, 0x20, 0x22, 0x22, 0x20, 0xAF, 0x20)) + number(load_address)),
        basic_line(30, bytes((0xF9, 0x20, 0xC0, 0x20)) + number(load_address)),
    ))
    code = binary.read_bytes()
    if not code:
        raise SystemExit("assembler produced an empty RAM image (likely address-space overflow)")
    image_end = load_address + len(code)
    if image_end > STACK_TOP:
        raise SystemExit(
            f"RAM image ends at ${image_end:04X}, overlapping stack reserved at ${STACK_TOP:04X}"
        )
    stream_data = stream.read_bytes()
    position = 14
    frame_sizes = []
    while position < len(stream_data):
        payload_length = struct.unpack_from("<I", stream_data, position + 1)[0]
        frame_sizes.append(5 + payload_length)
        position += 5 + payload_length
    mean_delta_size = sum(frame_sizes[1:]) / max(1, len(frame_sizes) - 1)
    headroom = STACK_TOP - image_end
    extra_frames = int(headroom // mean_delta_size) if mean_delta_size else 0
    capacity = {
        "load_address": load_address,
        "image_bytes": len(code),
        "image_end": image_end,
        "stack_top": STACK_TOP,
        "headroom_bytes": headroom,
        "measured_mean_delta_record_bytes": mean_delta_size,
        "estimated_additional_frames": extra_frames,
        "estimated_total_frames": len(frame_sizes) + extra_frames,
        "estimated_seconds_at_fps": (len(frame_sizes) + extra_frames) / args.fps,
    }
    (args.output / "ram_capacity.json").write_text(json.dumps(capacity, indent=2) + "\n")
    tap = bytearray()
    tap += tap_header(0, "SVDLOADER", len(basic), 10, len(basic))
    tap += tap_block(0xFF, basic)
    tap += tap_header(3, "SVDDEMO", len(code), load_address, 0x8000)
    tap += tap_block(0xFF, code)
    output_tap = args.output / "svd_ram_demo.tap"
    output_tap.write_bytes(tap)
    print(f"assembled {binary} ({len(code)} bytes)")
    print(
        f"RAM headroom below ${STACK_TOP:04X}: {headroom} bytes; "
        f"about {extra_frames} more measured-size delta frames"
    )
    print(f"wrote autostart TAP {output_tap} ({len(tap)} bytes)")


if __name__ == "__main__":
    main()
