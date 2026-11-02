<script lang="ts">
	import { goto } from '$app/navigation';
	import { Button, Command, LoadErrorBanner } from '@graphene/ui';
	import SearchIcon from '@lucide/svelte/icons/search';
	import Building2Icon from '@lucide/svelte/icons/building-2';

	import {
		canSearch,
		firmHref,
		firms as firmsOf,
		MIN_QUERY_LENGTH,
		scheduleQuery,
		SearchClient,
		searchUrl,
		settle
	} from '$lib/search';

	// Whether to offer the bar at all. `src/routes/search/+server.ts` and the API both require
	// `tenants:read`, so for an ops user without it the only thing this control could ever say is
	// "Search is unavailable just now." — it is hidden instead, exactly as the nav hides the
	// sections it gates. Read from the layout's own load (`+layout.server.ts` returns
	// `permissions`/`role` from `locals`), so it is the same data the API enforces on.
	//
	// Passed in as PROPS, not read from `page.data`. `page.data` merges layout and page data with the
	// page winning, and three role-editor pages (/model, /operations/roles, /tenants/[uid]/roles)
	// legitimately return their own `permissions` — the CATALOGUE of grantable slugs, not the slugs
	// this user holds. Reading the merged key made the bar vanish on exactly those routes, for every
	// ops user including a full `system` admin. The layout owns the principal, so it hands it over.
	let { permissions = [], role = null }: { permissions?: string[]; role?: string | null } =
		$props();
	const permitted = $derived(canSearch(permissions, role));

	let open = $state(false);
	let query = $state('');
	let firms = $state<ReturnType<typeof firmsOf>>([]);
	let loading = $state(false);
	let failed = $state(false);

	// Sequence-guarded (see $lib/search) so a slow earlier response can't overwrite a newer one.
	const client = new SearchClient(searchUrl);

	const trimmed = $derived(query.trim());
	const ready = $derived(trimmed.length >= MIN_QUERY_LENGTH);

	async function run(q: string): Promise<void> {
		loading = true;
		failed = false;
		const next = settle(await client.run(q));
		if (!next) return; // superseded by a newer query — leave the palette alone
		loading = false;
		firms = next.firms;
		failed = next.failed;
	}

	/** Back to the resting state, dropping anything in flight — the query is too short to search. */
	function idle(): void {
		client.cancel();
		firms = [];
		failed = false;
		loading = false;
	}

	// Debounced search on every keystroke; the cleanup `scheduleQuery` returns cancels the pending
	// call, so only the last pause hits the API.
	$effect(() => scheduleQuery(trimmed, { query: run, idle }));

	function select(id: string): void {
		open = false;
		query = '';
		firms = [];
		goto(firmHref(id));
	}
</script>

{#if permitted}
	<!-- The topbar only launches the palette; it is not itself an input. Keep it the same compact
	     utility control as the platform search so the staff portals share one piece of chrome. -->
	<Button
		variant="ghost"
		size="icon"
		aria-haspopup="dialog"
		aria-label="Search firms"
		title="Search firms"
		onclick={() => (open = true)}
		class="text-muted-foreground hover:text-foreground size-9 rounded-[var(--radius-chrome)]"
	>
		<SearchIcon class="size-4" />
	</Button>

	<!-- Filtering is the API's (RLS + tenants:read scoped), so the built-in cmdk filter is off. -->
	<Command.Dialog
		bind:open
		title="Search firms"
		description="Search across the firms on this instance"
		shouldFilter={false}
		onOpenChange={(o: boolean) => {
			if (!o) {
				query = '';
				firms = [];
				failed = false;
			}
		}}
	>
		<Command.Input bind:value={query} placeholder="Search firms…" />
		<Command.List>
			{#if failed}
				<LoadErrorBanner
					class="m-1"
					message="Search is unavailable just now."
					onRetry={() => run(trimmed)}
				/>
			{:else if !ready}
				<p class="text-muted-foreground px-3 py-6 text-center text-sm">
					Type at least {MIN_QUERY_LENGTH} characters to search by name, alias or website.
				</p>
			{:else if loading && firms.length === 0}
				<p class="text-muted-foreground px-3 py-6 text-center text-sm">Searching…</p>
			{:else if firms.length === 0}
				<Command.Empty forceMount>No matches for “{trimmed}”.</Command.Empty>
			{:else}
				<Command.Group heading="Firms">
					{#each firms as firm (firm.id)}
						<Command.Item value={firm.id} onSelect={() => select(firm.id)}>
							<Building2Icon class="text-muted-foreground size-4 shrink-0" />
							<span class="truncate">{firm.title}</span>
							{#if firm.subtitle}
								<span class="text-muted-foreground truncate pl-2 text-xs">{firm.subtitle}</span>
							{/if}
							{#if firm.meta}
								<span class="text-muted-foreground ml-auto shrink-0 pl-3 text-xs">{firm.meta}</span>
							{/if}
						</Command.Item>
					{/each}
				</Command.Group>
			{/if}
		</Command.List>
	</Command.Dialog>
{/if}
