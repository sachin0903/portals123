import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	server: {
		// Bind all interfaces and accept any Host, so a tenant served on a custom hostname works in
		// dev (e.g. a firm at `select-sei.local:5175` in /etc/hosts). The default localhost bind
		// resolves to ::1, so a custom name pointed at 127.0.0.1 is refused; `allowedHosts` then
		// stops Vite 403ing the unknown Host. HMR stays on localhost. Matches apps/client.
		host: true,
		allowedHosts: true,
		hmr: { host: 'localhost' },
		// Dev-only "hang" fix. This portal renders the heaviest trees (the journey detail, the
		// accounts record, the actions console), so the FIRST authenticated hit to each was a cold
		// Vite transform of hundreds of `.svelte` modules — @graphene/ui, every deep-imported Lucide
		// icon, the data tables, the flow graph — while the loader already redirected unauthenticated
		// probes, hiding it from a plain curl. `warmup` transforms the shell + the busiest routes at
		// server start (SSR and client pipelines both), so the cost is paid once at `dev` startup
		// instead of on the first visit to every page. Add a route here when a new heavy page lands.
		warmup: {
			ssrFiles: [
				'./src/routes/+layout.server.ts',
				'./src/routes/+layout.svelte',
				'./src/routes/journeys/[uid]/+page.svelte',
				'./src/routes/accounts/[uid]/+page.svelte',
				'./src/routes/actions/+page.svelte',
				'./src/routes/clients/[uid]/+page.svelte'
			],
			clientFiles: [
				'./src/routes/+layout.svelte',
				'./src/routes/journeys/[uid]/+page.svelte',
				'./src/routes/accounts/[uid]/+page.svelte',
				'./src/routes/actions/+page.svelte',
				'./src/routes/clients/[uid]/+page.svelte'
			]
		}
	},
	optimizeDeps: {
		// The flow graph's plain-JS deps (the run diagram + journey funnel). Left to on-demand
		// discovery, first opening a graph page made Vite find them mid-session, re-optimize and
		// force a full reload — the "it keeps hanging" part. Pre-bundling them at startup removes
		// that reload. The Svelte libraries stay out of here on purpose: vite-plugin-svelte owns
		// their handling, and the `resolve.dedupe` guard below must stay the only thing steering
		// Svelte resolution, or two runtimes blank the page.
		include: ['@dagrejs/dagre', '@xyflow/system']
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
			'layerchart',
			// The flow editor's node-graph lib: its internal subpath/dir imports aren't resolvable by
			// Node's native ESM under dev SSR (ERR_UNSUPPORTED_DIR_IMPORT) — Vite must bundle them.
			'@xyflow/svelte',
			'@xyflow/system',
			'@dagrejs/dagre'
		]
	}
});
