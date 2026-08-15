"""Simple bounded PackBits-style codec for complete ECM keyframe planes."""

from __future__ import annotations


def encode_packbits(data: bytes) -> bytes:
    output = bytearray()
    literal = bytearray()

    def flush_literal() -> None:
        while literal:
            count = min(len(literal), 128)
            output.append(count - 1)
            output.extend(literal[:count])
            del literal[:count]

    index = 0
    while index < len(data):
        run = 1
        while (index + run < len(data) and data[index + run] == data[index]
               and run < 130):
            run += 1
        if run >= 3:
            flush_literal()
            output.extend((0x80 | (run - 3), data[index]))
            index += run
        else:
            literal.extend(data[index:index + run])
            index += run
            if len(literal) >= 128:
                flush_literal()
    flush_literal()
    return bytes(output)


def decode_packbits(data: bytes, output_size: int) -> bytes:
    output = bytearray()
    index = 0
    while len(output) < output_size:
        if index >= len(data):
            raise ValueError("truncated PackBits stream")
        control = data[index]
        index += 1
        if control & 0x80:
            count = (control & 0x7F) + 3
            if index >= len(data):
                raise ValueError("truncated PackBits run")
            output.extend(bytes((data[index],)) * count)
            index += 1
        else:
            count = control + 1
            if index + count > len(data):
                raise ValueError("truncated PackBits literal")
            output.extend(data[index:index + count])
            index += count
        if len(output) > output_size:
            raise ValueError("PackBits stream exceeds requested output size")
    return bytes(output)
