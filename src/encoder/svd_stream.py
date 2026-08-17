"""Provisional, Z80-oriented SVD v0 stream encoder and reference decoder.

This bytecode is intentionally simple enough to profile before it is frozen.
All cell indices are raw TS display-file offsets, so bitmap and ECM attribute
addresses have the constant $2000 relationship used by the hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

from svd_ecm import ECMFrame, PLANE_SIZE, screen_offset

MAGIC = b"SVD0"
VERSION = 0
MODE_ECM = 1

FRAME_KEY = 1
FRAME_DELTA = 2
FRAME_REPEAT = 3
FRAME_SPARSE = 4
FRAME_XOR = 5
FRAME_HYBRID = 6

CMD_END = 0
CMD_SKIP = 1       # u16 count
CMD_BITMAP = 2     # u8 count (zero means 256), then bitmap bytes
CMD_ATTRIBUTE = 3  # u8 count (zero means 256), then attribute bytes
CMD_BOTH = 4       # u8 count (zero means 256), then bitmap/attribute pairs

HEADER = struct.Struct("<4sBBBBHI")
FRAME_HEADER = struct.Struct("<BI")


def _encode_xor_plane(previous: bytes, current: bytes) -> bytes:
    """Encode one raw 6144-byte plane as skip/literal XOR commands.

    00 ends a plane; 01..7f skip that many bytes; 80..ff carry 1..128
    literal XOR bytes. Gaps of up to two zero XOR bytes stay in a literal,
    avoiding a literal/skip/literal command triplet.
    """
    delta = bytes(a ^ b for a, b in zip(previous, current))
    output = bytearray()
    position = 0
    while position < PLANE_SIZE:
        if delta[position] == 0:
            end = position
            while end < PLANE_SIZE and delta[end] == 0 and end - position < 127:
                end += 1
            output.append(end - position)
            position = end
            continue
        end = position + 1
        last_nonzero = position
        while end < PLANE_SIZE and end - position < 128:
            if delta[end]:
                last_nonzero = end
            elif end - last_nonzero > 2:
                break
            end += 1
        end = last_nonzero + 1
        output.append(0x80 | (end - position - 1))
        output += delta[position:end]
        position = end
    output.append(0)
    return bytes(output)


def encode_xor(previous: ECMFrame, current: ECMFrame) -> tuple[bytes, FrameStats]:
    payload = (_encode_xor_plane(previous.bitmap, current.bitmap) +
               _encode_xor_plane(previous.attributes, current.attributes))
    return payload, FrameStats("XOR", len(payload))


def _decode_xor_plane(previous: bytes, payload: bytes, position: int) -> tuple[bytes, int]:
    output = bytearray(previous)
    destination = 0
    while position < len(payload):
        control = payload[position]
        position += 1
        if control == 0:
            return bytes(output), position
        if control < 0x80:
            destination += control
        else:
            count = (control & 0x7F) + 1
            if position + count > len(payload) or destination + count > PLANE_SIZE:
                raise ValueError("XOR literal exceeds plane or payload")
            for value in payload[position:position + count]:
                output[destination] ^= value
                destination += 1
            position += count
        if destination > PLANE_SIZE:
            raise ValueError("XOR skip exceeds plane")
    raise ValueError("unterminated XOR plane")


def decode_xor(previous: ECMFrame, payload: bytes) -> ECMFrame:
    bitmap, position = _decode_xor_plane(previous.bitmap, payload, 0)
    attributes, position = _decode_xor_plane(previous.attributes, payload, position)
    if position != len(payload):
        raise ValueError("trailing XOR payload")
    return ECMFrame(bitmap, attributes)


def encode_hybrid_plane(previous: bytes, current: bytes) -> bytes:
    """Optimal skip/literal/sparse-mask coding for one XOR plane."""
    if len(previous) != len(current):
        raise ValueError("hybrid planes must have equal length")
    delta = bytes(a ^ b for a, b in zip(previous, current))
    size = len(previous)
    costs = [0] * (size + 1)
    choices: list[tuple[str, int] | None] = [None] * size
    for position in range(size - 1, -1, -1):
        options = []
        if delta[position] == 0:
            run = 0
            while position + run < size and run < 127 and delta[position + run] == 0:
                run += 1
                options.append((1 + costs[position + run], "skip", run))
        for run in range(1, min(64, size - position) + 1):
            options.append((1 + run + costs[position + run], "literal", run))
        if position + 8 <= size:
            changed = sum(value != 0 for value in delta[position:position + 8])
            if changed:
                options.append((2 + changed + costs[position + 8], "mask", 8))
        cost, kind, run = min(options, key=lambda item: item[0])
        costs[position] = cost
        choices[position] = (kind, run)
    output = bytearray()
    position = 0
    while position < size:
        kind, run = choices[position]  # type: ignore[misc]
        if kind == "skip":
            output.append(run)
        elif kind == "literal":
            output.append(0x80 | (run - 1))
            output += delta[position:position + run]
        else:
            block = delta[position:position + 8]
            mask = sum((1 << (7 - index)) for index, value in enumerate(block) if value)
            output += bytes((0xC0, mask))
            output += bytes(value for value in block if value)
        position += run
    output.append(0)
    return bytes(output)


def encode_hybrid(previous: ECMFrame, current: ECMFrame) -> tuple[bytes, FrameStats]:
    payload = (encode_hybrid_plane(previous.bitmap, current.bitmap) +
               encode_hybrid_plane(previous.attributes, current.attributes))
    return payload, FrameStats("HYBRID", len(payload))


def encode_row_hybrid(previous: ECMFrame, current: ECMFrame) -> tuple[bytes, FrameStats]:
    """Hybrid XOR coding reset per raster row, bitmap then attribute.

    The small row terminator overhead permits the live ECM decoder to update
    detail and colour together from top to bottom instead of whole-plane order.
    """
    output = bytearray()
    for y in range(192):
        offset = screen_offset(y, 0)
        output += encode_hybrid_plane(previous.bitmap[offset:offset + 32],
                                      current.bitmap[offset:offset + 32])
        output += encode_hybrid_plane(previous.attributes[offset:offset + 32],
                                      current.attributes[offset:offset + 32])
    return bytes(output), FrameStats("ROW_HYBRID", len(output))


def _decode_hybrid_plane(previous: bytes, payload: bytes, position: int) -> tuple[bytes, int]:
    output = bytearray(previous)
    destination = 0
    while position < len(payload):
        control = payload[position]; position += 1
        if control == 0:
            return bytes(output), position
        if control < 0x80:
            destination += control
        elif control < 0xC0:
            count = (control & 0x3F) + 1
            if position + count > len(payload) or destination + count > PLANE_SIZE:
                raise ValueError("hybrid literal exceeds plane or payload")
            for value in payload[position:position + count]:
                output[destination] ^= value; destination += 1
            position += count
        elif control == 0xC0:
            if position >= len(payload) or destination + 8 > PLANE_SIZE:
                raise ValueError("hybrid mask exceeds plane or payload")
            mask = payload[position]; position += 1
            for bit in range(8):
                if mask & (0x80 >> bit):
                    if position >= len(payload):
                        raise ValueError("truncated hybrid mask values")
                    output[destination] ^= payload[position]; position += 1
                destination += 1
        else:
            raise ValueError("unknown hybrid command")
        if destination > PLANE_SIZE:
            raise ValueError("hybrid command exceeds plane")
    raise ValueError("unterminated hybrid plane")


def decode_hybrid(previous: ECMFrame, payload: bytes) -> ECMFrame:
    bitmap, position = _decode_hybrid_plane(previous.bitmap, payload, 0)
    attributes, position = _decode_hybrid_plane(previous.attributes, payload, position)
    if position != len(payload):
        raise ValueError("trailing hybrid payload")
    return ECMFrame(bitmap, attributes)


@dataclass(frozen=True)
class FrameStats:
    frame_type: str
    payload_bytes: int
    skip_commands: int = 0
    bitmap_commands: int = 0
    attribute_commands: int = 0
    both_commands: int = 0


def encode_sparse(previous: ECMFrame, current: ECMFrame) -> tuple[bytes, FrameStats]:
    bitmap_changes = [index for index in range(PLANE_SIZE) if previous.bitmap[index] != current.bitmap[index]]
    attr_changes = [index for index in range(PLANE_SIZE) if previous.attributes[index] != current.attributes[index]]
    output = bytearray(struct.pack("<H", len(bitmap_changes)))
    for offset in bitmap_changes:
        output += struct.pack("<HB", 0x4000 + offset, current.bitmap[offset])
    output += struct.pack("<H", len(attr_changes))
    for offset in attr_changes:
        output += struct.pack("<HB", 0x6000 + offset, current.attributes[offset])
    return bytes(output), FrameStats("SPARSE", len(output))


def decode_sparse(previous: ECMFrame, payload: bytes) -> ECMFrame:
    bitmap = bytearray(previous.bitmap)
    attrs = bytearray(previous.attributes)
    position = 0
    for base, plane in ((0x4000, bitmap), (0x6000, attrs)):
        if position + 2 > len(payload):
            raise ValueError("truncated sparse count")
        count = struct.unpack_from("<H", payload, position)[0]
        position += 2
        for _ in range(count):
            if position + 3 > len(payload):
                raise ValueError("truncated sparse record")
            address, value = struct.unpack_from("<HB", payload, position)
            position += 3
            offset = address - base
            if not 0 <= offset < PLANE_SIZE:
                raise ValueError("sparse address outside destination plane")
            plane[offset] = value
    if position != len(payload):
        raise ValueError("trailing sparse payload bytes")
    return ECMFrame(bytes(bitmap), bytes(attrs))


def encode_paired_cells(previous: ECMFrame, current: ECMFrame, *, reverse: bool = False) -> tuple[bytes, FrameStats]:
    """Encode replacement records pairing both planes in either raster direction."""
    records = bytearray()
    count = 0
    logical_cells = range(PLANE_SIZE - 1, -1, -1) if reverse else range(PLANE_SIZE)
    for logical in logical_cells:
        y, x_byte = divmod(logical, 32)
        offset = screen_offset(y, x_byte)
        flags = ((previous.bitmap[offset] != current.bitmap[offset]) |
                 ((previous.attributes[offset] != current.attributes[offset]) << 1))
        if not flags:
            continue
        records += struct.pack("<HB", offset, flags)
        if flags & 1:
            records.append(current.bitmap[offset])
        if flags & 2:
            records.append(current.attributes[offset])
        count += 1
    return struct.pack("<H", count) + records, FrameStats("PAIRED_CELLS", len(records) + 2)


def encode_sliced_paired_cells(previous: ECMFrame, current: ECMFrame,
                               slices: int, order: str = "interlaced") -> tuple[bytes, FrameStats]:
    """Encode raster slices that the player applies on successive 60 Hz ticks."""
    if not 2 <= slices <= 4:
        raise ValueError("sliced paired updates require 2 to 4 slices")
    if order not in ("interlaced", "bands"):
        raise ValueError("sliced paired order must be interlaced or bands")
    payload = bytearray([slices])
    total_records = 0
    for slice_index in range(slices):
        raster_rows = PLANE_SIZE // 32
        records = bytearray()
        count = 0
        if order == "interlaced":
            rows = range(slice_index, raster_rows, slices)
        else:
            y_start = (raster_rows * slice_index) // slices
            y_end = (raster_rows * (slice_index + 1)) // slices
            rows = range(y_start, y_end)
        for y in rows:
            for x_byte in range(32):
                offset = screen_offset(y, x_byte)
                flags = ((previous.bitmap[offset] != current.bitmap[offset]) |
                         ((previous.attributes[offset] != current.attributes[offset]) << 1))
                if not flags:
                    continue
                records += struct.pack("<HB", offset, flags)
                if flags & 1:
                    records.append(current.bitmap[offset])
                if flags & 2:
                    records.append(current.attributes[offset])
                count += 1
        payload += struct.pack("<H", count) + records
        total_records += count
    return bytes(payload), FrameStats("SLICED_PAIRED_CELLS", len(payload))


def decode_sliced_paired_cells(previous: ECMFrame, payload: bytes) -> ECMFrame:
    """Reference decoder for the concatenated counted raster slices."""
    if not payload or not 2 <= payload[0] <= 4:
        raise ValueError("invalid sliced paired-cell header")
    current = previous
    position = 1
    for _ in range(payload[0]):
        if position + 2 > len(payload):
            raise ValueError("truncated sliced paired-cell count")
        count = struct.unpack_from("<H", payload, position)[0]
        end = position + 2
        for _ in range(count):
            if end + 3 > len(payload):
                raise ValueError("truncated sliced paired-cell record")
            flags = payload[end + 2]
            if flags not in (1, 2, 3):
                raise ValueError("invalid sliced paired-cell flags")
            end += 3 + bool(flags & 1) + bool(flags & 2)
        current = decode_paired_cells(current, payload[position:end])
        position = end
    if position != len(payload):
        raise ValueError("trailing sliced paired-cell payload bytes")
    return current


def decode_paired_cells(previous: ECMFrame, payload: bytes) -> ECMFrame:
    if len(payload) < 2:
        raise ValueError("truncated paired-cell count")
    bitmap = bytearray(previous.bitmap)
    attrs = bytearray(previous.attributes)
    count = struct.unpack_from("<H", payload)[0]
    position = 2
    for _ in range(count):
        if position + 3 > len(payload):
            raise ValueError("truncated paired-cell record")
        offset, flags = struct.unpack_from("<HB", payload, position)
        position += 3
        if offset >= PLANE_SIZE or flags not in (1, 2, 3):
            raise ValueError("invalid paired-cell record")
        if flags & 1:
            if position >= len(payload):
                raise ValueError("truncated paired-cell bitmap")
            bitmap[offset] = payload[position]; position += 1
        if flags & 2:
            if position >= len(payload):
                raise ValueError("truncated paired-cell attribute")
            attrs[offset] = payload[position]; position += 1
    if position != len(payload):
        raise ValueError("trailing paired-cell payload bytes")
    return ECMFrame(bytes(bitmap), bytes(attrs))


def encode_paired_xor_cells(previous: ECMFrame, current: ECMFrame) -> tuple[bytes, FrameStats]:
    """Encode reversible raster-ordered cells, pairing bitmap and attribute XORs."""
    records = bytearray()
    count = 0
    for logical in range(PLANE_SIZE):
        y, x_byte = divmod(logical, 32)
        offset = screen_offset(y, x_byte)
        bitmap_xor = previous.bitmap[offset] ^ current.bitmap[offset]
        attribute_xor = previous.attributes[offset] ^ current.attributes[offset]
        flags = bool(bitmap_xor) | (bool(attribute_xor) << 1)
        if not flags:
            continue
        records += struct.pack("<HB", offset, flags)
        if flags & 1:
            records.append(bitmap_xor)
        if flags & 2:
            records.append(attribute_xor)
        count += 1
    return struct.pack("<H", count) + records, FrameStats("PAIRED_XOR_CELLS", len(records) + 2)


def decode_paired_xor_cells(previous: ECMFrame, payload: bytes) -> ECMFrame:
    if len(payload) < 2:
        raise ValueError("truncated paired-XOR cell count")
    bitmap = bytearray(previous.bitmap)
    attrs = bytearray(previous.attributes)
    count = struct.unpack_from("<H", payload)[0]
    position = 2
    for _ in range(count):
        if position + 3 > len(payload):
            raise ValueError("truncated paired-XOR cell record")
        offset, flags = struct.unpack_from("<HB", payload, position)
        position += 3
        if offset >= PLANE_SIZE or flags not in (1, 2, 3):
            raise ValueError("invalid paired-XOR cell record")
        if flags & 1:
            bitmap[offset] ^= payload[position]; position += 1
        if flags & 2:
            attrs[offset] ^= payload[position]; position += 1
    if position != len(payload):
        raise ValueError("trailing paired-XOR cell payload bytes")
    return ECMFrame(bytes(bitmap), bytes(attrs))


def _run_length(kinds: list[int], start: int, maximum: int) -> int:
    kind = kinds[start]
    end = start + 1
    row_limit = start + (32 - (start & 31))
    limit = min(len(kinds), start + maximum, row_limit)
    while end < limit and kinds[end] == kind:
        end += 1
    return end - start


def encode_delta(previous: ECMFrame, current: ECMFrame) -> tuple[bytes, FrameStats]:
    old_bitmap, old_attrs = previous.bitmap, previous.attributes
    new_bitmap, new_attrs = current.bitmap, current.attributes
    kinds = []
    for index in range(PLANE_SIZE):
        y, x_byte = divmod(index, 32)
        bitmap_offset = screen_offset(y, x_byte)
        bitmap_changed = old_bitmap[bitmap_offset] != new_bitmap[bitmap_offset]
        attr_changed = old_attrs[bitmap_offset] != new_attrs[bitmap_offset]
        kinds.append((1 if bitmap_changed else 0) | (2 if attr_changed else 0))
    if not any(kinds):
        return b"", FrameStats("REPEAT", 0)

    output = bytearray()
    counts = [0, 0, 0, 0]
    index = 0
    while index < PLANE_SIZE:
        kind = kinds[index]
        maximum = 65535 if kind == 0 else 256
        length = _run_length(kinds, index, maximum)
        counts[kind] += 1
        if kind == 0:
            output.append(CMD_SKIP)
            output += struct.pack("<H", length)
        else:
            output += bytes((kind + 1, length & 0xFF))
            offsets = [screen_offset(*divmod(cell, 32)) for cell in range(index, index + length)]
            if kind == 1:
                output += bytes(new_bitmap[offset] for offset in offsets)
            elif kind == 2:
                output += bytes(new_attrs[offset] for offset in offsets)
            else:
                for logical, bitmap_offset in zip(range(index, index + length), offsets):
                    output += bytes((new_bitmap[bitmap_offset], new_attrs[bitmap_offset]))
        index += length
    output.append(CMD_END)
    stats = FrameStats(
        "DELTA", len(output), counts[0], counts[1], counts[2], counts[3]
    )
    return bytes(output), stats


def decode_delta(previous: ECMFrame, payload: bytes) -> ECMFrame:
    bitmap = bytearray(previous.bitmap)
    attrs = bytearray(previous.attributes)
    source = 0
    cell = 0
    ended = False
    while source < len(payload):
        command = payload[source]
        source += 1
        if command == CMD_END:
            ended = True
            break
        if command == CMD_SKIP:
            if source + 2 > len(payload):
                raise ValueError("truncated SKIP command")
            length = struct.unpack_from("<H", payload, source)[0]
            source += 2
            if length == 0:
                raise ValueError("zero-length SKIP command")
            if (cell & 31) + length > 32:
                raise ValueError("SKIP crosses a raster row")
            cell += length
            continue
        if command not in (CMD_BITMAP, CMD_ATTRIBUTE, CMD_BOTH):
            raise ValueError(f"unknown SVD command {command}")
        if source >= len(payload):
            raise ValueError("truncated run length")
        length = payload[source] or 256
        if (cell & 31) + length > 32:
            raise ValueError("run crosses a raster row")
        source += 1
        data_length = length * (2 if command == CMD_BOTH else 1)
        if source + data_length > len(payload) or cell + length > PLANE_SIZE:
            raise ValueError("run exceeds payload or ECM plane")
        if command == CMD_BITMAP:
            for logical, value in enumerate(payload[source : source + length], start=cell):
                bitmap[screen_offset(*divmod(logical, 32))] = value
        elif command == CMD_ATTRIBUTE:
            for logical, value in enumerate(payload[source : source + length], start=cell):
                attrs[screen_offset(*divmod(logical, 32))] = value
        else:
            pairs = payload[source : source + data_length]
            for logical, value in enumerate(pairs[0::2], start=cell):
                bitmap[screen_offset(*divmod(logical, 32))] = value
            for logical, value in enumerate(pairs[1::2], start=cell):
                attrs[screen_offset(*divmod(logical, 32))] = value
        source += data_length
        cell += length
    if not ended or source != len(payload) or cell != PLANE_SIZE:
        raise ValueError("delta did not end exactly at plane boundary")
    return ECMFrame(bytes(bitmap), bytes(attrs))


def encode_stream(
    frames: list[ECMFrame], fps_num: int, fps_den: int = 1, delta_format: str = "runs"
) -> tuple[bytes, list[FrameStats]]:
    if not frames:
        raise ValueError("at least one frame is required")
    if not (1 <= fps_num <= 255 and 1 <= fps_den <= 255):
        raise ValueError("fps numerator and denominator must fit in one byte")
    output = bytearray(HEADER.pack(MAGIC, VERSION, MODE_ECM, fps_num, fps_den, 0, len(frames)))
    stats = []
    previous = None
    for frame in frames:
        if previous is None:
            payload = frame.bitmap + frame.attributes
            frame_type = FRAME_KEY
            item = FrameStats("KEY", len(payload))
        else:
            if frame == previous:
                payload, item, frame_type = b"", FrameStats("REPEAT", 0), FRAME_REPEAT
            elif delta_format == "sparse":
                payload, item = encode_sparse(previous, frame)
                frame_type = FRAME_SPARSE
            elif delta_format == "xor":
                payload, item = encode_xor(previous, frame)
                frame_type = FRAME_XOR
            elif delta_format == "hybrid":
                payload, item = encode_hybrid(previous, frame)
                frame_type = FRAME_HYBRID
            elif delta_format == "runs":
                payload, item = encode_delta(previous, frame)
                frame_type = FRAME_DELTA
            else:
                raise ValueError("delta_format must be 'runs', 'sparse', 'xor', or 'hybrid'")
        output += FRAME_HEADER.pack(frame_type, len(payload))
        output += payload
        stats.append(item)
        previous = frame
    return bytes(output), stats


def decode_stream(data: bytes) -> tuple[list[ECMFrame], tuple[int, int]]:
    if len(data) < HEADER.size:
        raise ValueError("truncated SVD header")
    magic, version, mode, fps_num, fps_den, flags, frame_count = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION or mode != MODE_ECM or flags != 0:
        raise ValueError("unsupported SVD stream")
    position = HEADER.size
    frames = []
    previous = None
    for _ in range(frame_count):
        if position + FRAME_HEADER.size > len(data):
            raise ValueError("truncated frame header")
        frame_type, payload_length = FRAME_HEADER.unpack_from(data, position)
        position += FRAME_HEADER.size
        end = position + payload_length
        if end > len(data):
            raise ValueError("truncated frame payload")
        payload = data[position:end]
        position = end
        if frame_type == FRAME_KEY:
            if len(payload) != PLANE_SIZE * 2:
                raise ValueError("invalid keyframe size")
            frame = ECMFrame(payload[:PLANE_SIZE], payload[PLANE_SIZE:])
        elif frame_type == FRAME_DELTA and previous is not None:
            frame = decode_delta(previous, payload)
        elif frame_type == FRAME_REPEAT and previous is not None and not payload:
            frame = previous
        elif frame_type == FRAME_SPARSE and previous is not None:
            frame = decode_sparse(previous, payload)
        elif frame_type == FRAME_XOR and previous is not None:
            frame = decode_xor(previous, payload)
        elif frame_type == FRAME_HYBRID and previous is not None:
            frame = decode_hybrid(previous, payload)
        else:
            raise ValueError("invalid frame type or missing predictor")
        frames.append(frame)
        previous = frame
    if position != len(data):
        raise ValueError("trailing bytes after SVD stream")
    return frames, (fps_num, fps_den)
