import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		// Bind all interfaces and accept any Host, so a tenant served on a custom hostname works in
		// dev (the default localhost bind resolves to ::1, so a name pointed at 127.0.0.1 is refused).
		// Matches apps/client and apps/platform. HMR stays on localhost.
		host: true,
		allowedHosts: true,
		hmr: { host: 'localhost' },
		// Pre-transform the shell + the heaviest operate routes at dev-server start, so the first
		// authenticated hit isn't a cold Vite compile of the shared @graphene/ui tree. Mirrors
		// apps/platform, where this removed a dev-only first-hit "hang". Cost moves to `dev` startup.
		warmup: {
			ssrFiles: [
				'./src/routes/+layout.server.ts',
				'./src/routes/+layout.svelte',
				'./src/routes/tenants/[uid]/operate/+page.svelte',
				'./src/routes/tenants/[uid]/operate/journeys/[journeyUid]/+page.svelte',
				'./src/routes/tenants/[uid]/operate/clients/[clientUid]/+page.svelte',
				'./src/routes/tenants/[uid]/operate/actions/+page.svelte'
			],
			clientFiles: [
				'./src/routes/+layout.svelte',
				'./src/routes/tenants/[uid]/operate/+page.svelte',
				'./src/routes/tenants/[uid]/operate/journeys/[journeyUid]/+page.svelte',
				'./src/routes/tenants/[uid]/operate/clients/[clientUid]/+page.svelte',
				'./src/routes/tenants/[uid]/operate/actions/+page.svelte'
			]
		}
	},
	resolve: {
		// ONE Svelte runtime in the browser bundle, whatever pnpm does upstream. bits-ui, runed,
		// svelte-toolbelt, mode-watcher, svelte-sonner and @lucide/svelte all take `svelte` as a PEER,
		// and a second copy is silent and catastrophic: two schedulers, so hydration clears every
		// {#if}/{#each} body and nothing refills it — the page flashes then blanks, popovers are inert,
		// `use:enhance` never attaches, and nothing is logged. Paired with the `svelte` override in
		// pnpm-workspace.yaml; this is the guard that survives a peer bump.
		dedupe: ['svelte', '@sveltejs/kit']
	},
	ssr: {
		// These ship .svelte source and reach SSR transitively via @graphene/ui, so
		// vite-plugin-svelte's auto-detection (direct deps only) misses them. Force Vite
		// to transform them instead of Node loading raw .svelte in dev SSR
		// (ERR_UNKNOWN_FILE_EXTENSION ".svelte").
		noExternal: [
			'@graphene/ui',
			'bits-ui',
			'svelte-toolbelt',
			'runed',
			'svelte-sonner',
			'mode-watcher',
			'@lucide/svelte',
			'layerchart'
		]
	}
});
