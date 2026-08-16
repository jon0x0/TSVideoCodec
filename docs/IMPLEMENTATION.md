# Implementation

## Initial keyframe

A complete ECM keyframe contains a 6144-byte bitmap and a 6144-byte attribute
plane. TAP and cartridge builds support raw storage and a bounded PackBits format;
`--keyframe-codec auto` chooses PackBits only when it saves at least 256 bytes.
Literal commands contain 1–128 bytes and repeated-byte commands expand to
3–130 bytes, so either compressed plane is guaranteed to fit in one 8 KB bank.

At startup the player clears `$6000-$77ff`, reconstructs the bitmap directly
into `$4000-$57ff`, and then reconstructs the attributes directly into the live
attribute plane. This prevents uninitialized colour from being paired with a
partly constructed bitmap and needs no temporary 6 KB RAM buffer. The builder
round-trips both compressed planes before packaging them.

## Display model

SVD-ECM models both 6 KB TS2068 display planes exactly. Each visible 8x1 cell
has one bitmap byte and one Spectrum-compatible attribute byte. The optimizer
tests legal paper/ink pairs and bit patterns against source RGB in linear-light
space. Preview images are rendered from the encoded bytes, not from an
intermediate approximation.

## Spatial encoding

The default Sierra Lite path diffuses quantization error through the image while
choosing legal two-colour ECM cells. Ordered patterns are available for stable
flat fields. Luma and chroma are scored separately so a numerically convenient
neutral pair does not erase pale source colour. Brightness, contrast,
saturation, gamma, and chroma weighting are explicit reproducible parameters.

### Source windows

Cropping is performed by FFmpeg before scaling, automatic clip analysis, or
ECM encoding. `--source-window X,Y,RIGHT` uses normalized source dimensions:
X and Y locate the upper-left corner and RIGHT locates the right edge.
`--source-window-pixels X,Y,WIDTH` uses source pixels instead. In both forms the
height is derived for a 4:3 viewport. If the rectangle would cross the source
edge, its width and height are reduced together while preserving the requested
origin. `sequence/run.json` records the requested values, source dimensions,
and resolved integer crop.

## Automatic analysis

`--auto` analyzes all selected source frames before encoding. It constructs a
robust temporal reference, measures cell variance and temporal distance, finds
stable connected regions, and distinguishes background restoration from moving
foreground material. The resulting static plate is source-derived; no scene
names or clip-specific masks are embedded in the encoder. Optional fixed-colour
or material treatments are generic command-line overrides and are disabled by
default.

The encoded reconstructionâ€”not the original source frameâ€”is always used as the
next predictor. This prevents encoder/decoder drift when rate control defers an
update. A cyclic warm-up can converge the predictor for seamless loops.

## Temporal coding and rate control

SVD supports keyframes and several delta representations. Hybrid deltas encode
bitmap and attribute runs independently for speed and density. Raster,
row-hybrid, and paired-cell transports trade additional bytes or decoder work
for more coherent updates of the live display. Rate control ranks candidate
8x1 updates by reduction in source error and can allocate a fixed per-frame,
per-plane, or whole-clip byte budget. Attribute age prevents colour changes
from being postponed indefinitely.

## Tearing control

The cartridge player begins an update immediately after a 60 Hz display tick.
Paired-cell transport walks in visible order and writes a cell's bitmap and
attribute together, avoiding a period in which new pixels use stale colours.
Presentation uses absolute hardware-tick deadlines so decode time does not
accumulate into playback slowdown. This reduces but cannot eliminate tearing:
the TS2068 is still scanning the same display memory being modified.

### Reversible bounce playback

`--bounce` is a player feature rather than a second encoding pass and is
available for cartridge and TAP output.
For N stored frames, the initial pointer table presents
`0,1,...,N-1,N-2,...,1`; subsequent cycles use the first delta in reverse to
return from frame 1 to frame 0. The player therefore exposes `2*N-2` timed
positions while storing only the keyframe and N-1 forward deltas.

Whole-plane hybrid records already carry XOR masks and are reversible. Normal
paired-cell records carry replacement bitmap and attribute values and must not
be replayed backward. Bounce combined with paired transport consequently uses
the dedicated paired-XOR player record (type 9). TAP bounce uses the same
record because its normal raster-replacement deltas are also directional. Each changed 8x1 cell holds
an offset, plane flags, and bitmap and/or attribute XOR masks. Applying the
record to either endpoint reconstructs the other endpoint while retaining the
anti-tearing benefit of updating both visible planes together.

## Storage and players

The 64 KB cartridge reserves one 8 KB bank for code and tables and uses seven
banks for media. The default packer keeps complete frame records within banks
and packs them best-fit-decreasing. Hybrid `--fifo-packing` instead treats all
seven banks as one 57,344-byte logical stream and inserts explicit continuation
markers at bank crossings. A full ECM keyframe is 12,288 bytes and may use
PackBits. The TAP player instead uses one safe contiguous RAM image and
preserves the BASIC workspace so a keypress can restore normal display mode
and return.

## Portability and verification

The reference encoder is Python/NumPy/Pillow. The performance-critical Sierra
Lite path has a C11 implementation with no third-party C dependencies. FFmpeg
performs all probing and extraction. Pasmo assembles Z80 players, and Fuse
automation measures real decoder duration and cadence. Unit tests cover address
mapping, legal ECM reconstruction, automatic analysis, source-window
resolution, stream round trips, reversible paired-XOR deltas, bounce table
traversal, and transport decoders.
