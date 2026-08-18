from pathlib import Path

import pytest

from src.audio2ay import event_bytes, load_sounds, parse_events


def write_sound(path: Path, *, channels: int = 3, interval: int = 1,
                blocks: int = 2) -> Path:
    payload = bytes(range(channels * blocks * 2))
    path.write_bytes(bytes((channels, interval)) + blocks.to_bytes(2, "little") + payload)
    return path


def test_loads_audio2ay_header_and_exact_payload(tmp_path):
    sound = load_sounds([write_sound(tmp_path / "effect.dat")])[0]
    assert (sound.channels, sound.tick_interval, sound.blocks) == (3, 1, 2)
    assert len(sound.data) == 16


def test_events_address_complete_bounce_timeline():
    # Twenty source frames produce playback frames 0..37 in bounce mode, so
    # frame 30 is a valid event on the reverse leg.
    events = parse_events(["10:1", "20:0", "30:1"], 2, 38)
    encoded = event_bytes(events, 38)
    assert encoded[10] == 2
    assert encoded[20] == 1
    assert encoded[30] == 2


def test_rejects_bad_audio_length_and_duplicate_frame(tmp_path):
    path = tmp_path / "bad.dat"
    path.write_bytes(bytes((3, 1, 2, 0, 1)))
    with pytest.raises(ValueError, match="expected 16"):
        load_sounds([path])
    with pytest.raises(ValueError, match="more than one"):
        parse_events(["2:0", "2:1"], 2, 10)
