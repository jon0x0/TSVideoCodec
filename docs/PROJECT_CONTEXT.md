# Project Context

The project explores video playback on the Timex Sinclair 2068, especially Extended Color Mode (ECM), native bank-switched cartridges, and later potentially smart PicoROM/RP2xxx cartridges.

Prior work achieved approximately 8.3 fps using conventional complete standard screens, but RAM capacity limited playback to about four frames. The current direction is to exploit TS2068-specific display hardware rather than merely copy complete Spectrum screens.

## Core observation
ECM provides two logically complementary planes:
- 6144-byte 256x192 1-bit bitmap: fine spatial detail / luminance-like information.
- 6144-byte extended attribute plane: one attribute per 8x1 pixel region, i.e. 32x192 color cells.

This resembles an extreme hardware-assisted luminance/chroma representation: retain high spatial bandwidth for structure/detail while color has lower horizontal bandwidth. It is not literally YCbCr/NTSC, but the perceptual analogy motivates the encoder.

Each 8x1 SCLD cell can be modeled as:
- one bitmap byte (8 selectors), and
- one attribute byte selecting the legal TS color pair/brightness state.

The desktop encoder can search legal color pairs and 8-bit masks to minimize perceptually weighted reconstruction error. Because encoding is offline, expensive optimization is acceptable.

## Temporal insight
For each 8x1 cell from frame N to N+1, the codec can choose:
1. retain bitmap + retain attribute,
2. change bitmap/detail only,
3. change attribute/chroma only,
4. change both.

This makes the stream SCLD-aware instead of a generic byte delta.

## Rate control
A high-performance CBR-like mode should optimize against decoder cost, not merely compressed byte count. Candidate changes can be ranked by visual-error reduction per estimated/measured Z80 T-state (or equivalent decode cost), accepting changes until the frame budget is consumed. This should maintain cadence while gracefully reducing image quality on complex frames.

## Second mode
Implement 32x24 standard attribute video. A raw frame is only 768 attribute bytes. Add temporal runs/deltas after establishing the fast raw path. This provides a high-cadence stylized video mode and a useful performance benchmark.

## Intended production profiles
- **SVD-HQ:** maximize ECM quality, accepting a lower cadence and relatively
  frequent detail-plane updates.
- **SVD-Motion / SVD-ECM-CBR:** prioritize stable high-performance motion with
  SCLD-aware deltas and cycle-budget rate control.
- **SVD-Fluid / SVD-ATTR:** prioritize cadence, using 32x24 attributes or
  fixed/slowly changing bitmap patterns where appropriate.

These are profiles of shared encoder/player infrastructure, not three unrelated
formats. Low-quality experimental codecs should only be built when they answer
a specific measurement question.

## Separate normal-screen benchmark
In normal TS2068 display mode, `$4000` and `$6000` are alternate display files
selected by the SCLD, permitting copy-to-hidden-screen then interrupt-aligned
flipping. ECM instead pairs those two bitmap-sized areas and has no equivalent
complete hidden display. A raw/delta normal-screen benchmark remains useful for
measuring cartridge and contended-memory throughput, but it is not the main SVD
codec direction.

## Storage/transport
Initial targets:
- RAM/TAP playback.
- Conventional TS2068 bank-switched DCK/physical cartridge, nominally 64K with 8K banks/chunks as supported by the user's existing cartridge workflow.

Later only:
- custom PicoROM/RP2040/RP2350 streaming from larger flash.

The bitstream and decoder should not depend on the future smart-cartridge transport.
