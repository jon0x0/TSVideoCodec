# Third-party software and resources

The embedded emulator is [TSRun](https://github.com/josef-jelinek/TSRun), by
Josef Jelinek. The Pages build bundles unmodified emulator modules, shaders,
audio worklet, mixer, and TS2068 system ROMs from
[TSRun commit 41dbe3e](https://github.com/josef-jelinek/TSRun/tree/41dbe3e41c692a1806f2cb900c5a0faa1549ae77).
The source README is included at `emulator/tsrun/README.md`. The runtime
checksums are recorded in `tsrun-lock.json`; generated copies live only in
the build artifact. TSRun and the original system ROMs retain their respective
authorship and rights.

The page's adapter and the downloadable DCK cartridge images are part of
[TSVideoCodec](https://github.com/jon0x0/TSVideoCodec).
