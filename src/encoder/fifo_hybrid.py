"""Command-aware bank-boundary packing for hybrid cartridge streams."""

from __future__ import annotations

CHUNK_SIZE = 0x2000
NEXT_BANK = 0xC1
NEXT_BANK_PADDED = 0xC2


def _commands(payload: bytes) -> list[tuple[str, bytes]]:
    result: list[tuple[str, bytes]] = []
    position = 0
    terminators = 0
    while position < len(payload):
        control = payload[position]
        position += 1
        if control == 0:
            result.append(("raw", bytes((0,))))
            terminators += 1
            if terminators == 2:
                break
        elif control < 0x80:
            result.append(("raw", bytes((control,))))
        elif control < 0xC0:
            count = (control & 0x3F) + 1
            result.append(("literal", payload[position:position + count]))
            position += count
        elif control == 0xC0:
            mask = payload[position]
            position += 1
            count = mask.bit_count()
            values = payload[position:position + count]
            position += count
            result.append(("mask", bytes((mask,)) + values))
        else:
            raise ValueError(f"reserved hybrid command ${control:02X}")
    if terminators != 2 or position != len(payload):
        raise ValueError("invalid two-plane hybrid payload")
    return result


def _encoded(command: tuple[str, bytes]) -> bytes:
    kind, data = command
    if kind == "raw":
        return data
    if kind == "literal":
        if not 1 <= len(data) <= 64:
            raise ValueError("hybrid literal outside 1..64 bytes")
        return bytes((0x80 | (len(data) - 1),)) + data
    return bytes((0xC0,)) + data


def _mask_literal(data: bytes) -> bytes:
    mask = data[0]
    values = iter(data[1:])
    return bytes(next(values) if mask & (1 << (7 - bit)) else 0 for bit in range(8))


def pack_fifo_hybrid(payload: bytes, absolute_offset: int) -> bytes:
    """Insert direct-decoder bank markers while filling every crossed bank."""
    commands = _commands(payload)
    output = bytearray()
    index = 0
    while index < len(commands):
        kind, data = commands[index]
        encoded = _encoded((kind, data))
        remaining = CHUNK_SIZE - ((absolute_offset + len(output)) % CHUNK_SIZE)
        more = index + 1 < len(commands)
        if len(encoded) < remaining or (len(encoded) == remaining and not more):
            output += encoded
            index += 1
            continue
        if remaining == 1:
            output.append(NEXT_BANK)
            continue
        if remaining == 2:
            output += bytes((NEXT_BANK_PADDED, 0))
            continue
        if kind == "mask":
            commands[index] = ("literal", _mask_literal(data))
            continue
        if kind != "literal":
            # A one-byte command can only fail when one byte remains, handled above.
            raise AssertionError("unexpected FIFO command boundary")
        take = remaining - 2
        output += _encoded(("literal", data[:take]))
        output.append(NEXT_BANK)
        commands[index] = ("literal", data[take:])
    return bytes(output)


def remove_fifo_markers(payload: bytes) -> bytes:
    """Test helper: remove markers from an already command-aligned FIFO stream."""
    output = bytearray()
    position = 0
    terminators = 0
    while position < len(payload) and terminators < 2:
        control = payload[position]
        position += 1
        if control == NEXT_BANK:
            continue
        if control == NEXT_BANK_PADDED:
            position += 1
            continue
        output.append(control)
        if control == 0:
            terminators += 1
        elif 0x80 <= control < 0xC0:
            count = (control & 0x3F) + 1
            output += payload[position:position + count]
            position += count
        elif control == 0xC0:
            mask = payload[position]
            count = mask.bit_count()
            output += payload[position:position + 1 + count]
            position += 1 + count
    return bytes(output)
