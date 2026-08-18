"""Validation and frame-event helpers for audio2ay sound-effect streams."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Audio2AYSound:
    path: Path
    data: bytes
    channels: int
    tick_interval: int
    blocks: int


def load_sounds(paths: list[Path]) -> list[Audio2AYSound]:
    sounds = []
    for supplied in paths:
        path = supplied.resolve()
        if not path.is_file():
            raise ValueError(f"audio2ay sound does not exist: {path}")
        data = path.read_bytes()
        if len(data) < 4:
            raise ValueError(f"audio2ay sound is shorter than its header: {path}")
        channels = data[0]
        interval = data[1]
        blocks = int.from_bytes(data[2:4], "little")
        if not 1 <= channels <= 3:
            raise ValueError(f"audio2ay sound has invalid channel count {channels}: {path}")
        if interval == 0:
            raise ValueError(f"audio2ay sound has zero tick interval: {path}")
        if blocks == 0:
            raise ValueError(f"audio2ay sound contains no blocks: {path}")
        expected = 4 + blocks * channels * 2
        if len(data) != expected:
            raise ValueError(
                f"audio2ay sound length is {len(data)}, expected {expected}: {path}")
        sounds.append(Audio2AYSound(path, data, channels, interval, blocks))
    return sounds


def parse_events(values: list[str], sound_count: int,
                 playback_frame_count: int) -> dict[int, int]:
    """Return zero-based playback-frame -> zero-based sound-index events."""
    events: dict[int, int] = {}
    for value in values:
        try:
            frame_text, sound_text = value.split(":", 1)
            frame, sound = int(frame_text), int(sound_text)
        except (ValueError, TypeError):
            raise ValueError(
                f"invalid audio2ay event {value!r}; expected FRAME:SOUND_INDEX") from None
        if not 0 <= frame < playback_frame_count:
            raise ValueError(
                f"audio2ay frame {frame} is outside playback timeline "
                f"0..{playback_frame_count - 1}")
        if not 0 <= sound < sound_count:
            raise ValueError(
                f"audio2ay sound index {sound} is outside 0..{sound_count - 1}")
        if frame in events:
            raise ValueError(f"more than one audio2ay sound is scheduled at frame {frame}")
        events[frame] = sound
    return events


def event_bytes(events: dict[int, int], playback_frame_count: int) -> bytes:
    """Encode events as 0=no event, otherwise one-based sound table index."""
    return bytes(events.get(frame, -1) + 1 for frame in range(playback_frame_count))
