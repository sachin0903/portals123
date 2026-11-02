// Vite's ambient types: the asset-URL import forms (`pdf.worker.min.mjs?url`, the
// `circle-flags/*.svg?no-inline` behind `currency-flags.ts`) and `import.meta.glob`. This package
// is consumed as SOURCE by the three SvelteKit apps, so Vite resolves these at their build and
// their checks already carry `vite/client` through `@sveltejs/kit`; this reference is for
// `svelte-check` run inside this package. It used to be a lone `declare module '*?url'` to avoid
// the whole of `vite/client` — `import.meta.glob` has no such local spelling, and a second
// `'*?url'` declaration beside Vite's would be a redeclaration, so the reference replaces it.
/// <reference types="vite/client" />
