import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src" / "encoder"))
from svd_ecm import ECMFrame, PLANE_SIZE, screen_offset
from keyframe_codec import decode_packbits, encode_packbits
from svd_stream import (decode_delta, decode_hybrid, decode_paired_cells, decode_sparse,
                        decode_stream, decode_xor, encode_delta, encode_hybrid,
                        encode_paired_cells, encode_sparse, encode_stream, encode_xor)


def random_frame(seed: int) -> ECMFrame:
    rng = random.Random(seed)
    return ECMFrame(rng.randbytes(PLANE_SIZE), rng.randbytes(PLANE_SIZE))


def test_delta_round_trip_all_command_types():
    old = bytearray(PLANE_SIZE)
    old_attr = bytearray(PLANE_SIZE)
    new = bytearray(old)
    new_attr = bytearray(old_attr)
    values = bytes(range(1, 11))
    for logical, value in zip(range(10, 20), values):
        new[screen_offset(*divmod(logical, 32))] = value
    for logical, value in zip(range(30, 40), values):
        new_attr[screen_offset(*divmod(logical, 32))] = value
    for logical, value in zip(range(50, 60), values):
        new[screen_offset(*divmod(logical, 32))] = value
    for logical, value in zip(range(50, 60), reversed(values)):
        new_attr[screen_offset(*divmod(logical, 32))] = value
    previous = ECMFrame(bytes(old), bytes(old_attr))
    current = ECMFrame(bytes(new), bytes(new_attr))
    payload, stats = encode_delta(previous, current)
    assert stats.bitmap_commands == stats.both_commands == 1
    assert stats.attribute_commands == 2  # logical 30..39 crosses a row boundary
    assert decode_delta(previous, payload) == current


def test_stream_key_delta_repeat_round_trip():
    first = random_frame(1)
    second_bitmap = bytearray(first.bitmap)
    second_bitmap[100] ^= 0xFF
    second = ECMFrame(bytes(second_bitmap), first.attributes)
    stream, stats = encode_stream([first, second, second], 24, 2)
    decoded, fps = decode_stream(stream)
    assert decoded == [first, second, second]
    assert fps == (24, 2)
    assert [item.frame_type for item in stats] == ["KEY", "DELTA", "REPEAT"]


def test_sparse_round_trip():
    first = random_frame(10)
    bitmap = bytearray(first.bitmap)
    attrs = bytearray(first.attributes)
    bitmap[3] ^= 0x55
    attrs[6000] ^= 0xAA
    second = ECMFrame(bytes(bitmap), bytes(attrs))
    payload, _ = encode_sparse(first, second)
    assert decode_sparse(first, payload) == second
    stream, _ = encode_stream([first, second], 12, delta_format="sparse")
    assert decode_stream(stream)[0] == [first, second]


def test_paired_cells_round_trip():
    first = random_frame(11)
    bitmap = bytearray(first.bitmap)
    attrs = bytearray(first.attributes)
    bitmap[screen_offset(3, 4)] ^= 0x55
    attrs[screen_offset(3, 4)] ^= 0x22
    bitmap[screen_offset(100, 20)] ^= 0x80
    second = ECMFrame(bytes(bitmap), bytes(attrs))
    payload, stats = encode_paired_cells(first, second)
    assert stats.frame_type == "PAIRED_CELLS"
    assert decode_paired_cells(first, payload) == second

    reverse_payload, _ = encode_paired_cells(first, second, reverse=True)
    assert decode_paired_cells(first, reverse_payload) == second
    # First reverse record is the lower changed raster cell.
    assert int.from_bytes(reverse_payload[2:4], "little") == screen_offset(100, 20)


def test_xor_round_trip():
    first = random_frame(20)
    bitmap = bytearray(first.bitmap)
    attrs = bytearray(first.attributes)
    bitmap[0] ^= 0x81
    bitmap[127:132] = b"abcde"
    attrs[-1] ^= 0x55
    second = ECMFrame(bytes(bitmap), bytes(attrs))
    payload, stats = encode_xor(first, second)
    assert stats.frame_type == "XOR"
    assert decode_xor(first, payload) == second
    stream, _ = encode_stream([first, second], 12, delta_format="xor")
    assert decode_stream(stream)[0] == [first, second]


def test_hybrid_round_trip():
    first = random_frame(30)
    bitmap = bytearray(first.bitmap)
    attrs = bytearray(first.attributes)
    for offset in (0, 7, 9, 100, 101, 102, 6143):
        bitmap[offset] ^= (offset + 1) & 0xFF
    attrs[200:230] = bytes(range(30))
    second = ECMFrame(bytes(bitmap), bytes(attrs))
    payload, stats = encode_hybrid(first, second)
    assert stats.frame_type == "HYBRID"
    assert decode_hybrid(first, payload) == second
    stream, _ = encode_stream([first, second], 12, delta_format="hybrid")
    assert decode_stream(stream)[0] == [first, second]


def test_z80_decoder_contract():
    root = Path(__file__).parents[1]
    subprocess.run([
        sys.executable, str(root / "src" / "tools" / "validate_decoder_contract.py")
    ], check=True)


def test_keyframe_packbits_round_trip_and_bounds():
    source = bytes([0] * 300 + list(range(256)) * 20 + [7] * 588)
    encoded = encode_packbits(source)
    assert decode_packbits(encoded, len(source)) == source
    assert len(encode_packbits(bytes(range(256)) * 24)) < 0x2000
