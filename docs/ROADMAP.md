# Focused Development Roadmap

## M0 - Integrate known-good hardware knowledge
- Obtain user's existing ECM display code.
- Obtain known-good TAP/DCK/cart workflow and assembler choice.
- Document exact TS2068 addresses/port values from proven code or authoritative references.
- Create tiny hardware/display abstraction around existing routines.

## M1 - Desktop ECM encoder and exact preview
- Decode source video via FFmpeg or extracted frames.
- Resize/filter to 256x192.
- Implement TS2068 palette/display reconstruction.
- Optimize each 8x1 cell for legal bitmap mask + attribute pair.
- Support luminance/chroma-weighted objective.
- Export raw bitmap/attribute frames and diagnostic previews.

Acceptance: one still frame and short sequence visually match the emulator/hardware representation.

## M2 - SCLD-aware temporal codec + Z80 decoder
- Implement KEY/DELTA/REPEAT.
- Independently code bitmap-only, attr-only, and both changes.
- Add skip/run commands.
- Hand-write and profile decoder in Z80 assembly.
- Generate RAM/TAP test stream.

Acceptance: short video plays in Fuse; collect real stream and timing statistics.

## M3 - Conventional 64K cartridge
- Pack decoder + video into user's existing DCK format/workflow.
- Handle 8K bank boundaries cheaply.
- Test in Fuse and physical cartridge if available.

Acceptance: same SVD stream logic works from native cartridge without PicoROM firmware modification.

## M4 - CBR / cycle-budget encoder
- Measure cost of each decoder command/path.
- Encoder chooses updates by visual benefit vs decode cost.
- Track reconstructed state exactly.
- Test multiple target frame rates/budgets.

Acceptance: stable cadence with graceful quality degradation on high-motion/scene-change frames.

## M5 - SVD-ATTR 32x24
- Raw 768-byte frame player first.
- Temporal skip/run/delta encoding.
- Target high cadence, potentially display-rate updates where practical.

## M6 - Refinements only if measurements justify them
- scene-change/keyframe policy,
- temporal chroma update reduction,
- temporal dithering,
- alternate perceptual metrics,
- AY/audio synchronization,
- larger/smart cartridge transport.

Avoid spending substantial time on intentionally low-quality codec variants unless needed as a benchmark.
