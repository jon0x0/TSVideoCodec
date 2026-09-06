#!/usr/bin/env python3
"""Build the static GitHub Pages artifact from web sources and curated demo media."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "build" / "pages"
DEMOS = (
    ("boing", "Boing Ball", "dck", ROOT / "demos" / "BoingDemo.dck"),
    ("juggler", "Juggler", "dck", ROOT / "demos" / "jugglerecm.dck"),
    ("newton", "Newton", "tap", ROOT / "demos" / "newton.tap"),
)


def main() -> None:
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    shutil.copytree(ROOT / "web", OUTPUT)
    assets = OUTPUT / "assets"
    assets.mkdir()
    manifest = {"format": 1, "demos": []}
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
