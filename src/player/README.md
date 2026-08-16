# Z80 Player workspace

Hand-optimized Z80 assembly is expected for decode hot paths.

Keep these layers separate:
1. SCLD/display initialization (adapt user's known-good ECM code).
2. Stream byte/block reader.
3. SVD command decoder.
4. Frame pacing/synchronization.
5. Transport implementations: RAM/TAP first, DCK banked source second.

Instrument/benchmark decoder command paths before freezing the stream format.

## Current decoder core

`svd_decoder.asm` implements the provisional RAM-source KEY and DELTA paths.
Build the decoder with the existing Pasmo 0.5.5 WSL binary and validate
bytecode constants against the executable Python specification with:

    python src/player/build_player.py

The script launches WSL with a clean `PATH`, avoiding invalid inherited Windows
path entries. `--native-pasmo` compiles and uses a Windows fallback when WSL is
unavailable.

The source uses IX/IY as paired bitmap/attribute cell pointers so all commands
advance through raster rows. Attributes are linear; a generated 192-entry table
selects each scrambled bitmap row.

Build the scripted three-frame autostart RAM/TAP demo with:

    python src/player/build_ram_demo.py ../video/input.mp4

Select a later source segment with `--start-seconds 3`.

The demo holds its last frame for approximately one second and then repeats
from its keyframe indefinitely.

Run it unattended in Fuse and compare all 12,288 displayed bytes with the
reference decoder's final frame:

    python src/player/validate_ram_demo.py

Measure KEY and DELTA execution paths using Fuse frame/T-state counters:

    python src/player/measure_decoder.py

## Contiguous TAP player

`build_video_tap.py` packages a sequence as a self-loading contiguous-RAM TAP.
The player preserves the BASIC workspace beneath the ECM attribute plane; any
key restores normal video mode, restores that workspace and the caller's stack,
and returns from `RANDOMIZE USR`.

Pass `--bounce` to play `0..N-1..1` repeatedly. The TAP stores only N source
frames and N-1 paired-XOR deltas. The same delta record is applied in each
direction, coupling bitmap and attribute changes to limit tearing without
duplicating reverse payload. The manifest reports both stored `frames` and
`playback_frame_count` (`2*N-2`). Bounce output remains subject to the TAP
player's safe contiguous-RAM capacity check.

Deterministic Fuse checks cover the completed display state, hardware cadence,
and keyboard exit restoration:

    python src/player/validate_video_tap.py build/example/tap build/example/sequence
    python src/player/measure_tap_cadence.py build/example/tap
    python src/player/validate_tap_exit.py build/example/tap
