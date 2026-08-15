# Test Plan

## Synthetic visual cases
- horizontal/vertical gradients
- fine black/white detail with slowly varying color
- moving high-contrast edge
- moving colored object on static background
- face/photo frame
- hard scene cut

## Metrics per frame
- source frame number/time
- encoded bytes
- command counts by type
- bitmap bytes changed
- attribute bytes changed
- estimated decoder T-states
- measured playback cadence where available
- keyframe/repeat flag
- reconstruction error metric(s)

## Regression outputs
Keep source, reconstructed preview, raw planes, encoded stream, decoder build hash/version, and emulator/hardware notes together.
