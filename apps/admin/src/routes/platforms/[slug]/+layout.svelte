<script lang="ts">
	// Chrome shared by every section of a platform. The sections are routes now (the sidebar
	// drills into them) rather than tabs, so the header, connection state and — critically — the
	// store live here: navigating Models → Lineage → Query must not rebuild the store, or the
	// canvas loses its pan/zoom and selection.
	import { Alert, AlertDescription, Badge, humanise, PageHeader, StatusBadge } from '@graphene/ui';
	import PlatformLive from '$lib/platforms/components/PlatformLive.svelte';
	import { setPlatformStore } from '$lib/platforms/context';
	import { emptyDataset, type ApiCatalogue } from '$lib/platforms/live';
	import { PlatformStore } from '$lib/platforms/store.svelte';
	import { STATUS_PULSES, STATUS_TONE, TRANSPORT_LABEL } from '$lib/platforms/status';

	let { data, children } = $props();

	// `applied` is deliberately plain (untracked): the effect must depend only on the loaded
	// data. Reading holder.store here would make it self-triggering, because replace() writes
	// the very state being compared (and the $state proxy never compares equal to the raw
	// loaded object anyway).
	// svelte-ignore state_referenced_locally
	let applied = data.dataset ?? emptyDataset(data.slug);
	// svelte-ignore state_referenced_locally
	let appliedSlug = data.slug;
	const holder = $state({ store: new PlatformStore(applied) });
	setPlatformStore(holder);

	// Before the DOM updates, so the live feed mounts against this platform's store
	// rather than the one the previous slug left behind.
	$effect.pre(() => {
		const dataset = data.dataset ?? emptyDataset(data.slug);
		if (dataset === applied && data.slug === appliedSlug) return;
		// Same platform, new data: keep the store (and its view state) and swap the data in.
		// Different platform: a fresh store, since none of the view state transfers.
		if (data.slug === appliedSlug) holder.store.replace(dataset);
		else holder.store = new PlatformStore(dataset);
		applied = dataset;
		appliedSlug = data.slug;
	});

	const meta = $derived(holder.store.data.meta);

	// Row counts, SQL and column lineage are a second request. The overview paints from the
	// layout load; this fills the model graph in once the catalogue scan finishes, including
	// after a live reload (a finished run invalidates the layout, which re-runs this effect).
	$effect(() => {
		const slug = data.slug;
		if (!data.dataset) return;
		const controller = new AbortController();
		void fetch(`/platforms/${slug}/catalogue`, { signal: controller.signal })
			.then((res) => (res.ok ? (res.json() as Promise<ApiCatalogue>) : null))
			.then((body) => {
				if (controller.signal.aborted || body?.status !== 'up') return;
				holder.store.applyCatalogue(body);
			})
			.catch(() => {});
		return () => controller.abort();
	});
</script>

<PageHeader
	title={meta.name}
	description={meta.description}
	back={{ href: '/platforms', label: 'All platforms' }}
>
	{#snippet actions()}
		<StatusBadge
			tone={STATUS_TONE[meta.connection.status]}
			pulse={STATUS_PULSES.has(meta.connection.status)}
			>{humanise(meta.connection.status)}</StatusBadge
		>
		{#each meta.transports as transport (transport)}
			<Badge variant="ghost" class="font-mono text-[11px]">{TRANSPORT_LABEL[transport]}</Badge>
		{/each}
	{/snippet}
</PageHeader>

<div class="space-y-6">
	<div class="text-muted-foreground -mt-4 flex flex-wrap items-center gap-3 text-sm">
		<span>{meta.connection.detail}</span>
		{#if data.dataset}
			{#key data.slug}
				<PlatformLive slug={data.slug} store={holder.store} />
			{/key}
		{/if}
	</div>

	{#if data.liveError}
		<Alert variant="destructive">
			<AlertDescription>
				Live data is unavailable: {data.liveError}. No cached data is shown below.
			</AlertDescription>
		</Alert>
	{/if}

	{@render children?.()}
</div>
