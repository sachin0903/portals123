// Vitest reads this in place of vite.config.ts, so the app's own vite config stays the
// `defineConfig` with no `test` block (docs/gotchas.md). A plain object literal for the same
// reason pkgs/ui's is: importing `vitest/config` pulls a conflicting vite's types.
//
// Two projects, as in pkgs/ui, because the two kinds of test need opposite module resolution:
//  - `server` — node. Every `*.test.ts` except the DOM ones.
//  - `client` — `*.dom.test.ts`, which MOUNT a component in jsdom. It needs the `browser` resolve
//    condition at the vite level (client AND ssr resolvers, since vitest loads dependencies
//    through its SSR pipeline), or `mount` from 'svelte' lands on the server build.
export default {
	test: {
		projects: [
			{
				extends: './vite.config.ts',
				test: {
					name: 'server',
					environment: 'node',
					include: ['src/**/*.test.ts'],
					exclude: ['src/**/*.dom.test.ts']
				}
			},
			{
				extends: './vite.config.ts',
				resolve: { conditions: ['browser'] },
				ssr: { resolve: { conditions: ['browser'] } },
				test: {
					name: 'client',
					environment: 'jsdom',
					include: ['src/**/*.dom.test.ts']
				}
			}
		]
	}
};
