#!/usr/bin/env python3
"""Build and validate the initial 64K TS2068 SVD cartridge transport."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "encoder"))
sys.path.insert(0, str(ROOT))
from toolchain import assemble_pasmo
from svd_ecm import ECMFrame
from svd_stream import encode_delta, encode_hybrid, encode_paired_cells, encode_row_hybrid
from svd_ecm import screen_offset
CHUNK_SIZE = 0x2000
STREAM_CHUNKS = (0, 1, 2, 3, 5, 6, 7)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("sequence", type=Path)
    parser.add_argument("stream", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pasmo", default=None,
                        help="Pasmo executable; defaults to PASMO or PATH")
    parser.add_argument("--loop-pause-frames", type=int, default=60)
    parser.add_argument("--seamless-loop", action="store_true")
    parser.add_argument("--stop-at-end", action="store_true",
                        help="hold the final frame indefinitely instead of restarting")
    parser.add_argument("--decode-tick-compensation", type=int, default=0,
                        help="scheduler ticks known to be missed by every delta decode")
    parser.add_argument("--raster-updates", action="store_true",
                        help="use top-to-bottom paired-cell deltas to reduce tearing")
    parser.add_argument("--row-hybrid-updates", action="store_true",
                        help="use compact top-to-bottom bitmap/attribute row deltas")
    parser.add_argument("--paired-cell-updates", action="store_true",
                        help="use raster-ordered replacement cells with paired bitmap/colour")
    parser.add_argument("--reverse-paired-cell-updates", action="store_true",
                        help="use bottom-to-top replacement cells with paired bitmap/colour")
    parser.add_argument("--frame-limit", type=int, default=0,
                        help="diagnostic limit; zero uses the complete sequence")
    args = parser.parse_args()
    if args.stop_at_end and args.seamless_loop:
        raise SystemExit("--stop-at-end and --seamless-loop are mutually exclusive")
    if sum((args.raster_updates, args.row_hybrid_updates, args.paired_cell_updates,
            args.reverse_paired_cell_updates)) > 1:
        raise SystemExit("update transport options are mutually exclusive")
    if not 0 <= args.decode_tick_compensation <= 255:
        raise SystemExit("--decode-tick-compensation must fit in one byte")
    args.output.mkdir(parents=True, exist_ok=True)
    prefixes = sorted(args.sequence.glob("frame_*.pix"))
    if args.frame_limit > 0:
        prefixes = prefixes[:args.frame_limit]
    if not prefixes:
        raise SystemExit("sequence contains no ECM frames")
    frames = []
    for prefix in prefixes:
        bitmap = prefix.read_bytes()
        attributes = prefix.with_suffix(".atr").read_bytes()
        if len(bitmap) != 0x1800 or len(attributes) != 0x1800:
            raise SystemExit("ECM frame planes must each be 6144 bytes")
        frames.append(bitmap + attributes)
    capacity = len(STREAM_CHUNKS) * CHUNK_SIZE

    payload = bytearray(b"\xFF" * capacity)

    def segments(offset: int, destination: int, length: int) -> list[tuple[int, int, int, int]]:
        result = []
        remaining = length
        while remaining:
            slot = offset // CHUNK_SIZE
            within = offset % CHUNK_SIZE
            chunk = STREAM_CHUNKS[slot]
            count = min(remaining, CHUNK_SIZE - within)
            # Cartridge chunks 2/3 are copied into underlying HOME RAM chunks
            # 6/7 at boot. This keeps the live ECM display planes mapped while
            # still making all 56 non-code cartridge KB useful for payload.
            if chunk in (2, 3):
                mask = 0x10
                source = (chunk + 4) * CHUNK_SIZE + within
            else:
                mask = 0x10 | (1 << chunk)
                source = chunk * CHUNK_SIZE + within
            result.append((mask, source, destination, count))
            offset += count
            destination += count
            remaining -= count
        return result

    frame_records = []
    frame_stats = []
    # Frame zero is a raw keyframe. Later frames are compact, independently
    # terminated bitmap and attribute XOR streams and never cross an 8K bank.
    payload[:0x3000] = frames[0]
    frame_records.append(("key", segments(0, 0x4000, 0x1800) +
                          segments(0x1800, 0x6000, 0x1800)))
    frame_stats.append({"frame": 0, "frame_type": "KEY", "update_bytes": 0x3000})
    blobs = []
    for index in range(1, len(frames)):
        previous = ECMFrame(frames[index - 1][:0x1800], frames[index - 1][0x1800:])
        current = ECMFrame(frames[index][:0x1800], frames[index][0x1800:])
        blob, _ = (encode_paired_cells(previous, current,
                                       reverse=args.reverse_paired_cell_updates)
                   if (args.paired_cell_updates or args.reverse_paired_cell_updates) else
                   encode_delta(previous, current) if args.raster_updates else
                   encode_row_hybrid(previous, current) if args.row_hybrid_updates else
                   encode_hybrid(previous, current))
        if len(blob) > CHUNK_SIZE:
            raise SystemExit(f"frame {index} compressed delta exceeds one bank")
        blobs.append((index, blob))
    loop_blob = None
    if args.seamless_loop:
        previous = ECMFrame(frames[-1][:0x1800], frames[-1][0x1800:])
        first = ECMFrame(frames[0][:0x1800], frames[0][0x1800:])
        loop_blob, _ = (encode_paired_cells(previous, first,
                                            reverse=args.reverse_paired_cell_updates)
                        if (args.paired_cell_updates or args.reverse_paired_cell_updates) else
                        encode_delta(previous, first) if args.raster_updates else
                        encode_row_hybrid(previous, first) if args.row_hybrid_updates else
                        encode_hybrid(previous, first))
        blobs.append((len(frames), loop_blob))

    # Best-fit decreasing placement keeps each delta in one source bank while
    # reclaiming holes that chronological packing leaves behind.
    slots = [[0x3000, 0x4000]] + [
        [slot * CHUNK_SIZE, (slot + 1) * CHUNK_SIZE] for slot in range(2, len(STREAM_CHUNKS))
    ]
    placements = {}
    for index, blob in sorted(blobs, key=lambda item: len(item[1]), reverse=True):
        candidates = [(end - start, slot_index) for slot_index, (start, end) in enumerate(slots)
                      if end - start >= len(blob)]
        if not candidates:
            raise SystemExit(f"frame {index} exceeds cartridge capacity")
        _, slot_index = min(candidates)
        offset = slots[slot_index][0]
        payload[offset:offset + len(blob)] = blob
        slots[slot_index][0] += len(blob)
        placements[index] = (offset, blob)

    for index, blob in blobs:
        offset, blob = placements[index]
        source = segments(offset, 0, len(blob))[0]
        if index < len(frames):
            frame_records.append(("paired" if (args.paired_cell_updates or
                                                args.reverse_paired_cell_updates) else
                                  "raster" if args.raster_updates else
                                  "row_hybrid" if args.row_hybrid_updates else "hybrid", source[:2]))
            frame_stats.append({"frame": index,
                                "frame_type": ("PAIRED_CELLS_REVERSE" if args.reverse_paired_cell_updates else
                                               "PAIRED_CELLS" if args.paired_cell_updates else
                                               "RASTER" if args.raster_updates else
                                               "ROW_HYBRID" if args.row_hybrid_updates else "HYBRID"),
                                "update_bytes": len(blob)})
        else:
            loop_record = source[:2]
    used_payload = 0x3000 + sum(len(blob) for _, blob in blobs)

    lines = [f"FRAME_COUNT     EQU     {len(frames)}", "FRAME_TABLE_PTRS:"]
    lines.append("                DW      " + ",".join(f"FRAME_{i}_TABLE" for i in range(len(frames))))
    for index, (kind, records) in enumerate(frame_records):
        lines.append(f"FRAME_{index}_TABLE:")
        if kind == "key":
            lines.append("                DB      1")
            for mask, source, destination, count in records:
                lines.append(f"                DB      ${mask:02X}\n"
                             f"                DW      ${source:04X},${destination:04X},${count:04X}")
            lines.append("                DB      0")
        else:
            mask, source = records
            record_type = 6 if kind == "paired" else 4 if kind == "raster" else 5 if kind == "row_hybrid" else 3
            lines.append(f"                DB      {record_type},${mask:02X}\n                DW      ${source:04X}")
    if args.seamless_loop:
        lines.append("LOOP_TABLE_PTRS:")
        lines.append("                DW      LOOP_FRAME_TABLE," +
                     ",".join(f"FRAME_{i}_TABLE" for i in range(1, len(frames))))
        mask, source = loop_record
        lines.append("LOOP_FRAME_TABLE:")
        loop_type = 6 if (args.paired_cell_updates or args.reverse_paired_cell_updates) else 4 if args.raster_updates else 5 if args.row_hybrid_updates else 3
        lines.append(f"                DB      {loop_type},${mask:02X}\n                DW      ${source:04X}")
    else:
        lines.append("LOOP_TABLE_PTRS EQU FRAME_TABLE_PTRS")
    (args.output / "frame_table.inc").write_text("\n".join(lines) + "\n", encoding="ascii")
    (args.output / "bitmap_rows.inc").write_text(
        "\n".join(f"                DW      ${0x4000 + screen_offset(y, 0):04X}"
                  for y in range(192)) + "\n", encoding="ascii")
    stream_metadata = json.loads(args.stream.with_suffix(".json").read_text())
    (args.output / "player_config.inc").write_text(
        f"TICK_NUMERATOR EQU {60 * stream_metadata['fps_den']}\n"
        f"TICK_DENOMINATOR EQU {stream_metadata['fps_num']}\n"
        f"LOOP_PAUSE_FRAMES EQU {args.loop_pause_frames}\n"
        f"STOP_AT_END EQU {1 if args.stop_at_end else 0}\n"
        f"DECODE_TICK_COMPENSATION EQU {args.decode_tick_compensation}\n",
        encoding="ascii")

    chunk4_path = args.output / "chunk4.bin"
    symbols = args.output / "cartridge_boot.symbols"
    assemble_pasmo(ROOT / "cartridge" / "cartridge_boot.asm", chunk4_path,
                   symbols, args.output, args.pasmo)
    chunk4 = chunk4_path.read_bytes()
    if len(chunk4) != CHUNK_SIZE:
        raise SystemExit(f"assembled chunk 4 is {len(chunk4)} bytes, expected 8192")
    if chunk4[:8] != bytes.fromhex("02 02 08 80 EF 01 00 00"):
        raise SystemExit("invalid AROS header")

    stream = args.stream.read_bytes()
    chunks = [bytearray(b"\xFF" * CHUNK_SIZE) for _ in range(8)]
    chunks[4][:] = chunk4
    position = 0
    bank_map = []
    for chunk in STREAM_CHUNKS:
        count = min(CHUNK_SIZE, len(payload) - position)
        if count > 0:
            chunks[chunk][:count] = payload[position:position + count]
            bank_map.append({
                "chunk": chunk,
                "address": chunk * CHUNK_SIZE,
                "raw_offset": position,
                "length": count,
            })
            position += count
    reconstructed = b"".join(bytes(chunks[item["chunk"]][:item["length"]]) for item in bank_map)
    if reconstructed != payload:
        raise SystemExit("banked update-payload readback mismatch")

    physical = b"".join(map(bytes, chunks))
    dck = bytes((0,)) + bytes((2,) * 8) + physical
    bin_path = args.output / "svd_video_64k.bin"
    dck_path = args.output / "svd_video_64k.dck"
    bin_path.write_bytes(physical)
    dck_path.write_bytes(dck)
    manifest = {
        "format": "TS2068 DOCK/AROS",
        "physical_bytes": len(physical),
        "dck_bytes": len(dck),
        "playback": (
            "banked hybrid bitmap/attribute delta streams, stop on final frame"
            if args.stop_at_end else
            ("banked hybrid bitmap/attribute delta streams, seamless loop"
             if args.seamless_loop and args.loop_pause_frames == 0
             else f"banked hybrid bitmap/attribute delta streams, {args.loop_pause_frames}-frame loop pause")
        ),
        "frame_count": len(frames),
        "seamless_loop": args.seamless_loop,
        "stop_at_end": args.stop_at_end,
        "decode_tick_compensation": args.decode_tick_compensation,
        "raster_updates": args.raster_updates,
        "row_hybrid_updates": args.row_hybrid_updates,
        "paired_cell_updates": args.paired_cell_updates,
        "reverse_paired_cell_updates": args.reverse_paired_cell_updates,
        "loop_delta_bytes": len(loop_blob) if loop_blob is not None else 0,
        "loop_pause_frames": args.loop_pause_frames,
        "interrupt_hz": 60,
        "tick_numerator": 60 * stream_metadata["fps_den"],
        "tick_denominator": stream_metadata["fps_num"],
        "update_payload_bytes": used_payload,
        "packed_cartridge_bytes": len(payload),
        "stream_bytes": len(stream),
        "update_payload_capacity": capacity,
        "frame_updates": frame_stats,
        "frame_chunks": bank_map,
        "milestone": "Z80-decoded banked hybrid XOR/mask stream",
    }
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {dck_path} ({len(dck)} bytes)")
    print(f"wrote {bin_path} ({len(physical)} bytes)")
    print(f"verified {len(frames)} frame updates ({used_payload} used bytes) across {len(bank_map)} chunks")


if __name__ == "__main__":
    main()
