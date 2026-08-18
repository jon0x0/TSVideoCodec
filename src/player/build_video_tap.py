#!/usr/bin/env python3
"""Build a contiguous-RAM TS2068 ECM video TAP from an encoded sequence."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "encoder"))
sys.path.insert(0, str(ROOT))
from toolchain import assemble_pasmo
from audio2ay import event_bytes, load_sounds, parse_events
from svd_ecm import ECMFrame, screen_offset
from keyframe_codec import decode_packbits, encode_packbits
from svd_stream import encode_delta, encode_paired_xor_cells
from progress import progress, progress_done

LOAD_ADDRESS = 0x7800
STACK_TOP = 0xFF00
BASIC_RAMTOP = 0xE7FF  # protected BASIC stack above the loaded video image
WORKSPACE_BACKUP = 0xE000


def number(value: int) -> bytes:
    return str(value).encode("ascii") + bytes((0x0E, 0, 0, value & 0xFF, value >> 8, 0))


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
    parser.add_argument("sequence", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--fps-num", type=int, default=130)
    parser.add_argument("--fps-den", type=int, default=11)
    parser.add_argument("--pasmo", default=None,
                        help="Pasmo executable; defaults to PASMO or PATH")
    parser.add_argument("--keyframe-codec", choices=("raw", "packbits", "auto"),
                        default="auto")
    parser.add_argument("--bounce", action="store_true",
                        help="reuse paired-XOR deltas for forward/reverse playback")
    parser.add_argument("--audio2ay", action="append", type=Path, default=[], metavar="FILE")
    parser.add_argument("--audio2ay-play", action="append", default=[],
                        metavar="FRAME:SOUND_INDEX")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    try:
        audio_sounds = load_sounds(args.audio2ay)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    frames = []
    for pix in sorted(args.sequence.glob("frame_*.pix")):
        frames.append(ECMFrame(pix.read_bytes(), pix.with_suffix(".atr").read_bytes()))
    if not frames:
        raise SystemExit("sequence contains no ECM frames")
    if args.bounce and len(frames) < 2:
        raise SystemExit("--bounce requires at least two frames")

    raw_key = frames[0].bitmap + frames[0].attributes
    packed_bitmap = encode_packbits(frames[0].bitmap)
    packed_attributes = encode_packbits(frames[0].attributes)
    packed_key = packed_bitmap + packed_attributes
    if (decode_packbits(packed_bitmap, 0x1800) != frames[0].bitmap or
            decode_packbits(packed_attributes, 0x1800) != frames[0].attributes):
        raise SystemExit("compressed keyframe failed round-trip verification")
    keyframe_codec = args.keyframe_codec
    if keyframe_codec == "auto":
        keyframe_codec = "packbits" if len(packed_key) + 256 < len(raw_key) else "raw"
    key_path = args.output / ("frame_000.keypack" if keyframe_codec == "packbits" else "frame_000.key")
    key_path.write_bytes(packed_key if keyframe_codec == "packbits" else raw_key)
    blobs = []
    for index in range(1, len(frames)):
        progress(f"Compressing TAP update frame {index}/{len(frames) - 1}")
        blob, _ = (encode_paired_xor_cells(frames[index - 1], frames[index])
                   if args.bounce else encode_delta(frames[index - 1], frames[index]))
        path = args.output / f"frame_{index:03d}.{'pairxor' if args.bounce else 'raster'}"
        path.write_bytes(blob); blobs.append(path)
    progress_done(f"Compressed {len(frames) - 1} TAP frame updates")
    loop_blob = b""
    if not args.bounce:
        loop_blob, _ = encode_delta(frames[-1], frames[0])
        (args.output / "loop.raster").write_bytes(loop_blob)
    playback_count = 2 * len(frames) - 2 if args.bounce else len(frames)
    if playback_count > 255:
        raise SystemExit("bounce playback exceeds the 255-entry TAP frame table")
    try:
        audio_events = parse_events(args.audio2ay_play, len(audio_sounds), playback_count)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    audio_payload_bytes = (sum(len(sound.data) for sound in audio_sounds) +
                           (playback_count + 2 * len(audio_sounds) if audio_sounds else 0))
    raster_payload_bytes = (key_path.stat().st_size + sum(path.stat().st_size for path in blobs) +
                            len(loop_blob) + audio_payload_bytes)
    safe_image_bytes = WORKSPACE_BACKUP - LOAD_ADDRESS
    if raster_payload_bytes + 1024 > safe_image_bytes:
        raise SystemExit(
            f"raster TAP needs at least {raster_payload_bytes + 1024} bytes, "
            f"safe contiguous image budget is {safe_image_bytes} bytes"
        )

    initial_indices = ([0] + list(range(1, len(frames))) + list(range(len(frames) - 1, 1, -1))
                       if args.bounce else list(range(len(frames))))
    loop_indices = ([1] + list(range(1, len(frames))) + list(range(len(frames) - 1, 1, -1))
                    if args.bounce else None)
    lines = [f"FRAME_COUNT     EQU     {playback_count}", "FRAME_TABLE_PTRS:",
             "                DW      " + ",".join(f"FRAME_{i}_TABLE" for i in initial_indices),
             "LOOP_TABLE_PTRS:",
             "                DW      " + (",".join(f"FRAME_{i}_TABLE" for i in loop_indices)
                                           if args.bounce else
                                           "LOOP_FRAME_TABLE," + ",".join(f"FRAME_{i}_TABLE" for i in range(1, len(frames)))),
             "FRAME_0_TABLE:", f"                DB      {7 if keyframe_codec == 'packbits' else 1}",
             "                DW      FRAME_0_DATA"]
    for index in range(1, len(frames)):
        lines += [f"FRAME_{index}_TABLE:", f"                DB      {9 if args.bounce else 4}",
                  f"                DW      FRAME_{index}_DATA"]
    if not args.bounce:
        lines += ["LOOP_FRAME_TABLE:", "                DB      4", "                DW      LOOP_FRAME_DATA"]
    lines += ["FRAME_0_DATA:", f'                INCBIN  "{key_path.name}"']
    for index in range(1, len(frames)):
        lines += [f"FRAME_{index}_DATA:",
                  f'                INCBIN  "frame_{index:03d}.{"pairxor" if args.bounce else "raster"}"']
    if not args.bounce:
        lines += ["LOOP_FRAME_DATA:", '                INCBIN  "loop.raster"']
    (args.output / "tap_frames.inc").write_text("\n".join(lines) + "\n", encoding="ascii")
    (args.output / "tap_config.inc").write_text(
        f"TICK_NUMERATOR EQU {60 * args.fps_den}\nTICK_DENOMINATOR EQU {args.fps_num}\n",
        encoding="ascii")
    (args.output / "bitmap_rows.inc").write_text(
        "\n".join(f"                DW      ${0x4000 + screen_offset(y, 0):04X}" for y in range(192)) + "\n",
        encoding="ascii")
    audio_lines = ["AUDIO_EVENT_TABLE:",
                   "                DB      " + ",".join(
                       str(value) for value in event_bytes(audio_events, playback_count)),
                   "AUDIO_SOUND_TABLE:"]
    if audio_sounds:
        audio_lines.append("                DW      " + ",".join(
            f"AUDIO_SOUND_{index}" for index in range(len(audio_sounds))))
        for index, sound in enumerate(audio_sounds):
            local = args.output / f"audio2ay_{index:03d}.dat"
            shutil.copyfile(sound.path, local)
            audio_lines += [f"AUDIO_SOUND_{index}:",
                            f'                INCBIN  "{local.name}"']
    else:
        audio_lines.append("                DW      0")
    (args.output / "audio2ay_config.inc").write_text(
        "\n".join(audio_lines) + "\n", encoding="ascii")

    binary = args.output / "svd_video_tap.bin"
    symbols = args.output / "svd_video_tap.symbols"
    assemble_pasmo(ROOT / "player" / "video_tap_player.asm", binary, symbols,
                   args.output, args.pasmo)
    code = binary.read_bytes()
    if not code:
        raise SystemExit("Pasmo produced an empty TAP image (address-space overflow)")
    end = LOAD_ADDRESS + len(code)
    if end > WORKSPACE_BACKUP:
        raise SystemExit(f"TAP RAM image ends at ${end:04X}, overlapping backup at ${WORKSPACE_BACKUP:04X}")

    basic = b"".join((
        basic_line(10, bytes((0xFD, 0x20)) + number(BASIC_RAMTOP)),
        basic_line(20, bytes((0xEF, 0x20, 0x22, 0x22, 0x20, 0xAF, 0x20)) + number(LOAD_ADDRESS)),
        basic_line(30, bytes((0xF9, 0x20, 0xC0, 0x20)) + number(LOAD_ADDRESS)),
    ))
    tap = bytearray(tap_header(0, "SVDLOADER", len(basic), 10, len(basic)))
    tap += tap_block(0xFF, basic)
    tap += tap_header(3, "SVDVIDEO", len(code), LOAD_ADDRESS, 0)
    tap += tap_block(0xFF, code)
    tap_path = args.output / "svd_video.tap"
    tap_path.write_bytes(tap)
    metadata = {"format": "TS2068 TAP", "frames": len(frames),
                "playback_frame_count": playback_count, "bounce": args.bounce,
                "delta_format": "paired-xor" if args.bounce else "raster-replacement",
                "fps_num": args.fps_num,
                "fps_den": args.fps_den, "load_address": LOAD_ADDRESS, "image_bytes": len(code),
                "basic_ramtop": BASIC_RAMTOP,
                "basic_workspace_backup": WORKSPACE_BACKUP,
                "keyframe_codec": keyframe_codec,
                "keyframe_raw_bytes": len(raw_key),
                "keyframe_stored_bytes": key_path.stat().st_size,
                "audio2ay": [{"index": index, "source": str(sound.path),
                               "bytes": len(sound.data), "channels": sound.channels,
                               "tick_interval": sound.tick_interval, "blocks": sound.blocks}
                              for index, sound in enumerate(audio_sounds)],
                "audio2ay_events": {str(frame): index for frame, index in audio_events.items()},
                "image_end": end, "stack_top": STACK_TOP, "headroom_bytes": STACK_TOP - end,
                "tap_bytes": len(tap), "keyboard_exit": "any key; restores normal video and returns to BASIC"}
    (args.output / "tap_manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"assembled contiguous TAP image: {len(code)} bytes, ${LOAD_ADDRESS:04X}-${end - 1:04X}")
    print(f"RAM headroom below stack: {STACK_TOP - end} bytes")
    print(f"wrote {tap_path} ({len(tap)} bytes)")


if __name__ == "__main__":
    main()
