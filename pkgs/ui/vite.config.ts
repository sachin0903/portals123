import { svelte } from '@sveltejs/vite-plugin-svelte';
import { fileURLToPath } from 'node:url';

// Vitest reads this. The svelte plugin is here so unit tests can import modules that pull in
// .svelte files (portal-pages.ts imports lucide icon components); without it vitest dies with
// "Unknown file extension .svelte". Apps compiling this package as source use their own vite
// configs — this one only affects commands run inside pkgs/ui. Deliberately no `defineConfig`
// import: the CLAUDE.md gotcha (vitest/config type conflicts) applies, and `vite` is not a
// direct dependency of this package — so the `test` block below is a PLAIN object literal,
// typed by nothing, which is what keeps that gotcha respected.
//
// Two projects, because the two kinds of test need opposite module resolution and cannot share
// one environment:
//  - `server` — the bulk. Pure-function tests and `*.ssr.test.ts` files that call `render` from
//    `svelte/server`, which needs components compiled for SSR and node conditions.
//  - `client` — `*.dom.test.ts`. Mounts a component in jsdom to observe behaviour that only
//    exists at run time (a timer-driven re-render, an event handler). It needs the `browser`
//    resolve condition, or `import { mount } from 'svelte'` lands on the server build and throws
//    `lifecycle_function_unavailable`. That condition CANNOT be global: it would compile the SSR
//    suite's components for the client and break every `render()` in it.
// Splitting them was the only way to cover the one thing neither could: `live-age.dom.test.ts`
// (a relative timestamp that must tick) regressed silently because this package had no way to
// mount anything.
export default {
	plugins: [svelte()],
	resolve: {
		alias: {
			'$app/forms': fileURLToPath(new URL('./src/lib/test-stubs/app-forms.ts', import.meta.url)),
			'$app/navigation': fileURLToPath(
				new URL('./src/lib/test-stubs/app-navigation.ts', import.meta.url)
			),
			'$app/state': fileURLToPath(new URL('./src/lib/test-stubs/app-state.ts', import.meta.url))
		}
	},
	test: {
		// The static-analysis suites here are DISK-bound: they walk `apps/*` and the workspace
		// `node_modules` to prove cross-package invariants (one Svelte runtime, every srcdoc frame
		// sandboxed, every portal that toasts mounts a Toaster). Milliseconds idle, seconds when the
		// machine is busy — and the pre-push hook runs the API's whole suite alongside them, where
		// three of them blew the 5s default and reported their invariant as BROKEN rather than slow.
		// The scans are hoisted out of the timed bodies too; this is the margin, not the fix.
		testTimeout: 30_000,
		// beforeAll that first-imports a chart (layerchart) is the same disk-bound wait as the
		// bodies above. The default 10s hook budget reported a passing import as a timeout when
		// the pre-push hook ran the API suite alongside it.
		hookTimeout: 30_000,
		projects: [
			{
				extends: './vite.config.ts',
				// Vite-level, NOT under `test`: vitest loads node_modules deps through its SSR
				// pipeline, so both the client resolver and the SSR one must prefer `browser` or
				// `svelte` keeps resolving to `index-server.js`.
				resolve: { conditions: ['browser'] },
				ssr: { resolve: { conditions: ['browser'] } },
				test: {
					name: 'client',
					environment: 'jsdom',
					include: ['src/**/*.dom.test.ts']
				}
			},
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.test.ts'],
					exclude: ['src/**/*.dom.test.ts']
				}
			}
		]
	}
};
