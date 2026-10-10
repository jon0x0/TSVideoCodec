#!/usr/bin/env python3
"""Build the static GitHub Pages artifact from web sources and curated demo media."""

from __future__ import annotations

import hashlib
import argparse
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "pages"
TSRUN_REVISION = "41dbe3e41c692a1806f2cb900c5a0faa1549ae77"
RUNTIME_FILES = (
    "machine.js", "z80.js", "tape.js", "dock.js", "ay.js", "screen.js",
    "sound.js", "sound.worklet.js", "keyboard.js", "joystick.js",
    "screen.vert.glsl", "screen.frag.glsl", "keyboard.png",
    "roms/ts2068-0.rom", "roms/ts2068-1.rom", "README.md",
)
DEMOS = (
    ("boing", "Boing Ball", "dck", ROOT / "demos" / "BoingDemo.dck"),
    ("juggler", "Juggler", "dck", ROOT / "demos" / "jugglerecm.dck"),
    ("newton", "Newton", "tap", ROOT / "demos" / "newton.tap"),
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tsrun", type=Path, required=True,
                        help=f"Clean TSRun checkout at {TSRUN_REVISION}")
    args = parser.parse_args()
    lock = json.loads((ROOT / "web" / "tsrun-lock.json").read_text())
    if lock["revision"] != TSRUN_REVISION or set(lock["files"]) != set(RUNTIME_FILES):
        raise SystemExit("TSRun lock does not match the required runtime")
    for name in RUNTIME_FILES:
        data = (args.tsrun / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != lock["files"][name]:
            raise SystemExit(f"Pinned TSRun checksum mismatch: {name}")
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    shutil.copytree(ROOT / "web", OUTPUT)
    runtime = OUTPUT / "emulator" / "tsrun"
    for name in RUNTIME_FILES:
        target = runtime / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.tsrun / name, target)
    # TSRun resolves its worklet relative to the document, not sound.js.
    shutil.copyfile(args.tsrun / "sound.worklet.js", OUTPUT / "emulator" / "sound.worklet.js")
    assets = OUTPUT / "assets"
    assets.mkdir()
    manifest = {"format": 1, "tsrun_revision": TSRUN_REVISION, "demos": []}
    for demo_id, title, media_type, source in DEMOS:
        data = source.read_bytes()
        if media_type == "dck" and len(data) != 65545:
            raise SystemExit(f"{source} is {len(data)} bytes; expected a 65,545-byte DCK")
        destination = assets / source.name
        destination.write_bytes(data)
        manifest["demos"].append(
            {
                "id": demo_id,
                "title": title,
                "type": media_type,
                "file": f"assets/{source.name}",
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    (assets / "demo-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Built {OUTPUT} with {len(DEMOS)} playable demos")


if __name__ == "__main__":
    main()
