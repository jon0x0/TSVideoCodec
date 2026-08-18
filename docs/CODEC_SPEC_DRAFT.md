# SVD Codec Specification - Draft v0

This is intentionally provisional. Profile decoder cost before freezing opcodes.

## Profiles

### SVD-ECM
High-quality TS2068 Extended Color video.
State is the currently displayed 6144-byte bitmap plane plus 6144-byte ECM attribute plane.

### SVD-ECM-CBR
Same reconstruction model, but desktop encoder selects updates under a per-frame decoder budget. Prefer measured Z80 cycle budget over raw byte budget.

The executable encoder exposes this separately as `--max-hybrid-bytes`; zero
disables it. Per-frame budget mode ranks complete 8x1 cell updates
by reduction in source-image error and applies the highest-value prefix that
fits. All later decisions compare against that reconstructed state.

An additional optional profile budgets bitmap and ECM attributes independently.
A measured split of 1450/350 compressed bytes with four-frame age priority for
deferred attributes improved both reconstructed RGB error and packed size on
the original test clip compared with a shared 1800-byte budget, while retaining
the same Z80 stream and decoder. These are historical measurements, not current
universal defaults.

The global allocator treats all delta payload as one clip budget, carries quiet
frame savings forward, weights allocation by source motion, and enforces a hard
per-frame ceiling. A historical 42,000-byte / 3,600-byte-cap profile fit all 25
frames of its test clip and remained below that build's 12 fps decoder deadline.
Front-end `--fill-space` now derives the clip budget from the selected
keyframe, seamless-loop delta, FIFO overhead, and 57,344-byte media capacity.

### SVD-ATTR
32x24 attribute video. Primary state is 768 attribute bytes. Bitmap may be fixed/preloaded or optionally use a later dither/detail extension.

The current executable compatibility profile uses a fixed alternating `AA/55`
bitmap and expands each of the 768 attributes over eight ECM scanlines, then
uses the existing ECM delta transports and TAP/cartridge players. This proves
end-to-end playback but does not yet realize compact 768-byte state on the wire.

An implemented 32x192 attribute profile uses the same fixed bitmap but selects
6144 independent 8x1 attributes. It preserves vertical colour resolution and
also produces attribute-only deltas after the initial keyframe.

## Proposed frame types
- KEY: establish complete state.
- DELTA: update current displayed state in place.
- REPEAT: no display-state change for this presentation interval.

## Candidate ECM delta operations
Do not freeze encoding until assembly profiling.
- SKIP_SHORT / SKIP_LONG: advance cell index without writes.
- BITMAP_RUN: consecutive bitmap-byte replacements.
- ATTR_RUN: consecutive ECM attribute replacements.
- BOTH_RUN: consecutive paired bitmap+attribute replacements.
- END_FRAME.
- Optional later: bitmap XOR run if measured faster/smaller overall; repeated value/pattern run; compact isolated-cell opcodes.

## Provisional SVD v0 bytecode

The executable reference format currently uses explicit commands so Z80 paths
can be implemented and measured before compact opcode allocation is frozen:

- `00`: end frame.
- `01 ll hh`: skip 1-65535 cells.
- `02 nn data...`: bitmap run, where `nn=0` means 256 cells.
- `03 nn data...`: attribute run.
- `04 nn bitmap,attribute...`: paired run.

Frames are KEY (raw bitmap then attributes), DELTA, or zero-payload REPEAT.
Cell indices are raster ordered. Commands never cross a 32-cell raster row.
See `src/encoder/svd_stream.py` for the authoritative executable draft. This layout
is not frozen until corresponding Z80 paths are profiled.

### Executable XOR experiment

The passive-cartridge player now implements a compact raw-offset XOR stream for
each plane independently. `00` ends a plane, `01..7F` skips 1..127 unchanged
bytes, and `80..FF` carries 1..128 literal XOR bytes. Bitmap and attribute
streams are concatenated, each with its own terminator. Frames remain inside
one 8K source bank for a small decoder hot loop; frame zero remains a raw
12,288-byte keyframe. This is an executable measured experiment, not yet the
final frozen SVD profile.

The next measured variant adds `C0 mask values...`, covering eight destination
bytes and carrying XOR values only for set mask bits. A dynamic-programming
encoder chooses skips, 1..64-byte literals, and sparse masks per plane. On the
current 19-frame sample this remains within the 12 fps deadline, but its worst
measured decode is close enough to the limit that further opcodes require
decoder-cost-aware rate control.

### Paired replacement and paired-XOR player records

The cartridge and TAP transports also support raster-ordered changed-cell lists. A
paired replacement record stores a 16-bit display-plane offset, flags selecting
bitmap and/or attribute, and the selected target byte values. It reduces visible
bitmap/colour mismatch during forward playback but is directional: replaying it
does not reconstruct the previous frame.

Bounce playback uses record type 9, paired-XOR cells, with the same count,
offset, and flag structure but XOR masks in place of replacement values. Given
adjacent reconstructed frames A and B and mask D, `A XOR D = B` and
`B XOR D = A`. A single stored record can therefore be referenced in both table
directions. For N stored frames, the player presents `2*N-2` positions without
duplicating frames or delta payload. This is currently a player transport
record rather than a frozen SVD v0 stream opcode.

Cartridge record type 11 wraps the same reversible paired-XOR cells in the
type-10 sliced container: one slice-count byte followed by that many counted
paired-XOR streams. The player waits for a display tick between streams. Both
`interlaced` and `bands` are packer ordering policies; no order flag is needed
because every record carries absolute display-plane offsets.

### Measured sparse-delta experiment

Frame type 4 stores two counted lists of absolute-address/value records: bitmap
changes followed by attribute changes. Each record is three bytes. This is less
byte-dense than an ideal compact run format, but its Z80 loop avoids row mapping
and expensive command dispatch. It is retained as a measured speed baseline.

A cell index ranges over 6144 8x1 cells. Bitmap and ECM attribute bytes use
the same Spectrum display-file permutation at `$4000+offset` and
`$6000+offset`; the SCLD pairs bytes having the same low 13-bit offset.
Constraining runs to one raster row makes both destinations contiguous inside
the hot loop; the decoder changes bitmap row address between rows.

## Encoder objective
For source cell x and previous reconstructed state p, consider candidate legal representations r and transmission action a.

Conceptually minimize:

    J = perceptual_error(source, reconstruction(r)) + lambda * decode_cost(a)

For fixed-budget mode, maximize visual-error reduction subject to total measured/estimated decode cost <= frame budget.

### Implemented search decomposition

The current implementation does not jointly brute-force `r` and `a` over a
whole frame. It uses three bounded stages:

- exhaustive evaluation of 128 legal bright/paper/ink attributes within each
  8x1 cell, followed by Sierra Lite bitmap decisions;
- visual-benefit ranking of the 6144 candidate cell updates and binary search
  over the ranked prefix when a byte ceiling applies; and
- an exact backward dynamic program choosing hybrid skip, literal-XOR, or
  sparse-mask commands for each fixed bitmap and attribute delta plane.

Thus the hybrid byte stream is minimum-sized for the selected reconstruction
under the implemented opcodes, while selection of the reconstruction under a
budget remains a practical heuristic rather than a globally optimal subset
search. Unrestricted frames bypass the second stage.

## Perceptual encoding
Test explicit luminance/chroma weighting. Preserve high-frequency structure preferentially in the 1-bit bitmap while accepting reduced horizontal chroma resolution. Do not assume raw RGB squared error is optimal.

The encoder should be able to exhaustively or efficiently search legal TS2068 color-pair + 8-bit-mask combinations for each 8x1 source region.

## Temporal reconstruction
Rate control must score against the *reconstructed previous frame*, not the original previous source frame. If an update is deferred, that retained state becomes the predictor for the next frame. This avoids encoder/decoder drift.

## Keyframes
Use periodic or scene-change keyframes as needed for random access/recovery and cartridge organization. Frequency should be empirical.

## Cartridge packing
Keep stream commands from awkwardly crossing 8K source-bank boundaries, or define a cheap explicit bank continuation mechanism. The desktop packer can spend space to make the Z80 decoder faster.

The implemented optional FIFO hybrid transport takes the latter approach. It
packs media contiguously across all seven 8 KB media banks and inserts reserved
continuation markers only at command boundaries. The decoder keeps a logical
bank-slot/address cursor, so no capacity is lost to whole-frame bin packing.

## Optional audio2ay event layer

TAP and cartridge containers may carry validated audio2ay sound-effect records.
Each record begins with channel count, 60 Hz tick interval, and a little-endian
block count, followed by `blocks * channels` two-byte AY values. A playback-cycle
event table stores zero for no trigger or sound index plus one. Event indices
address the logical player timeline, including reverse positions generated by
bounce playback. Audio storage is container payload, not part of SVD frame
delta syntax, so codec, transport, and AY scheduling remain separable.
