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

### Attribute-video profiles

`--video-mode attr-32x24` and `attr-32x192` use a stationary alternating
`AA/55` checker bitmap and search all 128 legal ECM attributes for the closest
equal ink/paper mixture. The 32x24 profile averages 8x8 source blocks and
repeats each result for eight scanlines. The 32x192 profile averages 8x1 cells
and chooses every scanline independently. Bitmap bytes therefore never change
after the keyframe and subsequent records naturally contain attributes only.

This initial implementation reuses the verified ECM stream and players. It
stores repeated 32x24 attributes in the existing 6144-byte logical plane; a
future compact profile can send only 768 attributes to a specialized player.

### What is searched

Spatial conversion, temporal rate control, and delta serialization use three
different searches and should not be conflated:

1. For each 8x1 ECM cell, the native Sierra Lite encoder evaluates all 128
   legal attribute choices (two brightness levels times eight paper colours
   times eight ink colours). It scores how closely the ink-paper line segment
   represents the eight linear-light source pixels, including configured
   temporal penalties, then performs causal Sierra Lite error diffusion to
   choose the bitmap pixels. This is exhaustive locally, not across a frame.
2. When rate control is active, all 6144 candidate cell updates are ranked by
   the reduction they make in reconstructed RGB squared error. A binary search
   repeatedly serializes prefixes of that ranking to find the largest prefix
   within the byte budget. This takes about `log2(6144)` trial encodes rather
   than testing every subset. It is a rate-distortion heuristic, not a globally
   optimal knapsack solution.
3. Once the reconstructed frame is fixed, each hybrid XOR plane is serialized
   by backward dynamic programming. At every byte position the encoder compares
   all legal zero skips, 1-64 byte literal runs, and eight-byte sparse masks.
   This produces the minimum-size representation available from the current
   hybrid command set for that fixed delta.

The first two steps can be lossy: ECM palette restrictions approximate the
source, and rate control may retain cells from the previous reconstruction.
The third step is lossless and reconstructs exactly the frame selected by the
encoder. Paired-cell transports use direct raster-ordered records instead and
do not perform the hybrid dynamic-programming search.

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

The static plate can be disabled independently with `--no-auto-static-plate`.
Its representation and sensitivity are controlled by `--auto-plate-encoder`,
`--auto-material-dither`, and `--background-motion-threshold`; stable-cell
temporal protection is tuned with `--background-penalty-multiplier`.

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

Native hybrid rate control tracks every cell whose candidate bitmap *or*
attribute is still missing. The one-command native automatic path defaults to
`--max-cell-age 4`; at that age the cell is forced into the next update.
`--max-cell-age 0` disables this and `--cell-age-bonus` tunes the preference
before the deadline. Tracking both planes prevents an old object silhouette
remaining in the bitmap after motion exposes the background.

`--max-frames N` is applied by FFmpeg before analysis or ECM conversion.
`--frame-selection first` stops after the first N samples, while `even` changes
the sampling interval so only N frames spread over the remaining duration are
passed to the encoder.

The normal front-end default is `--quality 100`, which leaves the spatial ECM
candidate unrestricted. Lower quality values derive a per-frame hybrid-byte
target from that frame's unrestricted delta size; the rate-distortion ranking
then retains the most valuable cells within that target. Thus quality is a
monotonic control scale, not a promise that RGB error changes linearly with the
percentage. Explicit expert byte ceilings remain available but have no hidden
nonzero default.

`--fill-space` is output-neutral. It saves untouched candidates, measures the
actual TAP raster and/or chosen cartridge transport, and binary-searches a
shared clip budget. Every search iteration starts from the untouched candidate
sequence, avoiding cumulative loss from repeatedly fitting an already fitted
result. For paired cartridge records, both total capacity and the current 8 KB
single-record limit are enforced.

The ranked-prefix search deliberately scores against the previous
*reconstructed* frame. A deferred update remains part of the next predictor,
so the desktop reconstruction and Z80 decoder cannot drift apart. Whole-clip
allocation weights frames by measured motion and carries unused capacity
forward. `--fill-space` first checks whether the unrestricted sequence fits;
if so, it preserves every candidate update and skips lossy rate control.

## Tearing control

The cartridge player begins an update immediately after a 60 Hz display tick.
Paired-cell transport walks in visible order and writes a cell's bitmap and
attribute together, avoiding a period in which new pixels use stale colours.
Presentation uses absolute hardware-tick deadlines so decode time does not
accumulate into playback slowdown. This reduces but cannot eliminate tearing:
the TS2068 is still scanning the same display memory being modified.

### Two-tick staged updates

`--transport paired --update-slices 2` partitions changed cells into two
raster slices. By default, alternate scanlines are assigned to each slice so
the intermediate state retains structure across the whole image;
`--slice-order bands` selects upper/lower bands for diagnosis. Record type 10 contains a slice count followed by two
ordinary counted paired-cell streams. The player decodes the upper band,
temporarily restores HOME ROM mapping so the IM 1 vector is valid, waits for
the next 60 Hz tick, restores the media bank, and decodes the lower band.
Bitmap and attribute remain paired within every changed 8x1 cell.

The normal presentation deadline still applies to the logical frame. At 30
fps the bands consume the two available 60 Hz periods; at lower rates the
completed frame is held for the remaining ticks. The mode reduces each write
burst but exposes one coherent partially updated state. It is opt-in, cartridge-only,
limited to paired transport and 30 fps or below, and currently excludes bounce.

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
PackBits. Front-end `--fill-space` preserves an untouched unrestricted
candidate sequence and repeatedly rate-controls a separate reconstruction.
Each probe measures the actual TAP raster or selected cartridge transport,
including keyframe and loop records. TAP and cartridge can therefore use the
same capacity policy, and `--format both` selects a reconstruction fitting both.
The TAP player instead uses one safe contiguous RAM image and
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

## Algorithm provenance

The spatial conversion direction was informed by Factus10's
[Retro Pixel Converter](https://github.com/factus10/retro-pixel-converter):
TS2068 8x1 ECM constraints, linear-light palette-pair fitting, and the use of
Sierra Lite for still-image quantization. Sierra Lite itself is a published
error-diffusion kernel rather than a codec-specific invention. TSVideoCodec's
C and Python implementations were written for this project; they do not import
the converter's browser source.

Temporal penalties, stable-region analysis, reconstructed-state prediction,
ranked byte-budget selection, hybrid XOR bytecode and its dynamic-programming
serializer, paired and row transports, reversible bounce records, FIFO bank
continuations, and the Z80 anti-tearing players are TSVideoCodec components.
