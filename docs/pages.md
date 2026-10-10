# Building and checking the demo gallery

The deployment bundles unmodified TSRun runtime files and system ROMs from
commit `41dbe3e41c692a1806f2cb900c5a0faa1549ae77`. Browser playback uses
only this site's files; an upstream outage or API change cannot change an
already deployed player. `web/tsrun-lock.json` verifies every bundled file.
Runtime files and generated demo assets remain under ignored `build/`.

From the repository root:

```sh
git -c core.autocrlf=false clone https://github.com/josef-jelinek/TSRun build/tsrun
git -C build/tsrun checkout 41dbe3e41c692a1806f2cb900c5a0faa1549ae77
python scripts/build_pages.py --tsrun build/tsrun
npm install --ignore-scripts --no-audit --no-fund
npx playwright install chromium
node tests/verify_tsrun_demos.mjs build/pages/emulator/tsrun
node tests/verify_pages.mjs
```

The browser test serves `/TSVideoCodec/` to check project-relative URLs,
blocks external runtime requests, and checks startup, each demo's animation,
audio context startup, switching, reset, and a direct demo selection link.
It can also check a deployment:

```sh
node tests/verify_pages.mjs https://jon0x0.github.io/TSVideoCodec/
```

Pull requests run both emulator and browser checks. Publishing on main runs
the same checks before uploading any Pages artifact, so a failed candidate
leaves the previous deployment in place. Update the pinned revision and
file checksums together when intentionally upgrading TSRun, then rerun both
checks. The audio initializer takes emulated frames per second, not sample
rate; its queue API is `soundWantsFrame`.

Demo links use `?demo=boing`, `?demo=juggler`, and `?demo=newton`.
