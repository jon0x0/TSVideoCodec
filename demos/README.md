# Curated demos

This is the only directory in the repository that intentionally contains
compiled runnable artifacts. `.dck` files are 64 KB TS2068 cartridge images for
Fuse or compatible hardware; `.tap` files are tape-loadable RAM players.

The scripts under `demos/scripts/` document the human-curated source selection
and encoder settings used during development. Source media and generated build
trees are not distributed in the repository.

## Play in a browser

The curated DCK cartridges and Newton TAP are embedded in the
[TSVideoCodec playable demo gallery](https://jon0x0.github.io/TSVideoCodec/).
The gallery imports the live
[TSRun emulator](https://github.com/josef-jelinek/TSRun) from its original site;
no emulator or system ROM copy is stored in this repository. Run
`python scripts/build_pages.py` to reproduce the static deployment artifact and
its cartridge hash manifest under the ignored `build/pages/` directory.
