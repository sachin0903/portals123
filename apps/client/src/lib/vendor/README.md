# Vendored third-party

## liquidGL.js

- Source: https://github.com/naughtyduk/liquidGL (`scripts/liquidGL.js`), commit `main` @ 2026-06.
- Licence: MIT © NaughtyDuk.
- Why vendored: not published to npm. Do **not** hand-edit — re-pull from upstream to update.
- Runtime dep: `html2canvas` (npm), attached to `window.html2canvas` by our loader before init.
- Loaded client-only via `$lib/LiquidGlass.svelte` (it attaches `window.liquidGL`).
