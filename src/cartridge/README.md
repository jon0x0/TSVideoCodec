# 64 KB cartridge transport

`build_cartridge.py` creates both a Fuse-loadable DCK and an exact 65,536-byte
physical cartridge image. The current player boots through AROS, plays a
generated ECM keyframe followed by separately terminated bitmap/attribute hybrid
streams from all seven non-code DOCK chunks at 12 fps deadlines, pauses for one
second after the final frame, and repeats.

Cartridge chunks 2 and 3 are copied at boot into underlying HOME RAM chunks 6
and 7; the $4000/$6000 regions can then return to the live ECM display while
their cartridge storage remains usable. Chunk 4 contains the executing player
and tables, giving 56 KB of media payload plus 8 KB of code/table storage.
Each compressed delta remains within one 8K bank. The hot loop interprets skip,
literal-XOR, and sparse eight-byte-mask commands against both display planes. Updates begin
just after an interrupt; a five-interrupt absolute deadline prevents decode
time from being added to the presentation interval. Temporal Sierra hysteresis
further reduces the changed-byte count and visible update interval.

The default packer uses best-fit-decreasing placement for complete delta
frames, retaining the fastest no-bank-crossing decoder path. The optional
`--fifo-packing` hybrid transport instead treats all seven media banks as one
logical 56 KB stream. It records each frame's starting slot and address and
advances automatically through cartridge and boot-shadow banks, eliminating
whole-frame fragmentation and the 8 KB per-frame storage restriction.

The FIFO byte reader is intentionally a separate option: on the 30-frame
dinosaur measurement it used the same 39,237 payload bytes without stranded
bank holes, reconstructed the final screen exactly, and measured 42,183–344,888
T-states per delta versus 21,586–183,052 for bank-local hybrid decoding.

The unrestricted profile and optional CBR-style profile are separate. Passing
`--max-hybrid-bytes 1800` to the sequence encoder currently fits all 25 source
frames remaining after the three-second start point; omitting it retains the
unrestricted quality behavior.

The optional independent-plane build uses `--max-bitmap-bytes 1450`,
`--max-attribute-bytes 350`, and `--max-attribute-age 4`. It also fits all 25
frames and is emitted separately under `build/cartridge_64k_planes`.

The globally allocated variant is emitted under `build/cartridge_64k_global`.
It uses a 42,000-byte aggregate delta budget, carries unused bytes forward, and
caps any one frame at 3,600 bytes to preserve measured decoder timing.

## Seamless loop build

`demos/scripts/build_kahnankas_loop.py` samples the complete 15.4-second source at a
hardware-even 7.5 fps, builds a distinct compressed last-to-first delta table,
and disables the extra loop pause. The first pass uses the keyframe; later
passes substitute the loop delta for frame zero, avoiding another raw keyframe
overwrite at the wrap. Pass `--validate` to run Fuse accuracy and timing checks.

`demos/scripts/build_kahnankas_13frame.py` preserves the selected 13-frame structure.
It uses `demos/scripts/export_kahnankas_frames.py` to select an evenly timed, phase-optimized
1.1-second cycle and records the exact 50 Hz source indices. It then presents
the frames at 130/11 fps using a fractional 60 Hz schedule. The 0.01 maximum-detail
variant is retained for comparison, but the real-time cartridge uses the 0.08
stream because its measured decoder fits the 20 ms frame interval.

Passing `--raster-updates` selects a separate low-tearing transport. Its delta
commands follow visible scanline order and update each 8x1 bitmap byte together
with its ECM attribute when both change. This avoids the scattered `0,8,16...`
scanline pattern and bitmap/color separation of the faster hybrid-plane decoder.
For the 13-frame loop it uses 25,251 payload bytes and measures 21.4--29.0 ms per
delta, versus 16,578 bytes and 3.5--9.0 ms for the default hybrid build.

## TAP/RAM transport

`src/player/build_video_tap.py` builds the raster stream as one contiguous RAM image
loaded at `$7800`, with an autostart BASIC loader. The Kahnankas wrapper exposes
this separately through `--tap-output`; cartridge output remains the default.
For example:

```
python demos/scripts/build_kahnankas_13frame.py --tap-output build/kahnankas_13frame_1_1s_tap --validate
```

The loader reserves a protected copy of the complete `$6000-$77FF` region at
`$E000-$F7FF`. AROS may place both the live `USR` stack and other BASIC working
state inside the memory used as the ECM attribute plane. All 6K are copied
before ECM begins and restored byte-for-byte on exit. The player waits for the autoload key to be released, then any
subsequent key ends playback. Exit selects normal SCLD mode directly, restores
the ROM video-state byte and BASIC stack, and returns from `USR`. Unlike the
banked cartridge decoder, the TAP keeps ROM interrupts enabled during RAM delta
decoding, so `$5C78` continues to track hardware frames; the steady 13-frame
loop is exactly 66 TS2068 frames (about 1.10 seconds).
