# Implementation

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

## Storage and players

The 64 KB cartridge reserves one 8 KB bank for code and tables and uses seven
banks for complete, non-crossing frame records. A full ECM keyframe is 12,288
bytes; subsequent deltas are packed best-fit-decreasing into the remaining
banks. The TAP player instead uses one safe contiguous RAM image and preserves
the BASIC workspace so a keypress can restore normal display mode and return.

## Portability and verification

The reference encoder is Python/NumPy/Pillow. The performance-critical Sierra
Lite path has a C11 implementation with no third-party C dependencies. FFmpeg
performs all probing and extraction. Pasmo assembles Z80 players, and Fuse
automation measures real decoder duration and cadence. Unit tests cover address
mapping, legal ECM reconstruction, automatic analysis, stream round trips, and
transport decoders.
