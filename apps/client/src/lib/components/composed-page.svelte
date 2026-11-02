<script lang="ts">
	import { LoadErrorBanner, PortalPage, Skeleton, type ListingQuery } from '@graphene/ui';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { completeTask } from '$lib/complete-task';
	import { listingHref, listingQueryOf } from '$lib/listing-url';
	import { loadErrorSummary } from '$lib/load-errors';
	import type { ComposedPage } from '$lib/page-data';

	// The one way a composed portal page renders in this app: title, the shared PortalPage grid,
	// the listing toolbars' URL round-trip and the action-item completion wiring. Home, the
	// [slug] route and the four listing routes all pass through here so behaviour cannot drift.
	// Home may stream `composed` as a promise so the shell HTML can flush first.
	let { composed }: { composed: ComposedPage | Promise<ComposedPage> } = $props();

	let completing = $state<string | null>(null);
	async function oncompletetask(uid: string): Promise<void> {
		completing = uid;
		try {
			await completeTask(uid);
		} finally {
			completing = null;
		}
	}

	function pageHref(n: number): string {
		const params = new URLSearchParams(page.url.searchParams);
		params.set('page', String(n));
		return `?${params.toString()}`;
	}

	const listingQuery = $derived(listingQueryOf(page.url));

	function onlistingchange(next: ListingQuery): void {
		const replace = (next.q ?? '') !== (listingQuery.q ?? '');
		void goto(listingHref(page.url, next), {
			keepFocus: true,
			noScroll: true,
			replaceState: replace
		});
	}
</script>

<!-- The browser tab is titled by the root layout, which resolves the same firm-defined page label
     from the slug in the URL. It cannot be titled from here: `<svelte:head>` is illegal inside a
     block, and a streamed page has no label until its data lands — the layout has one on the click. -->
{#await Promise.resolve(composed)}
	<!-- The shape of the page, not a spinner: the widgets are a masonry of cards, so three card-sized
	     blocks say "your portal is arriving here" while a centred message says only "wait". -->
	<div class="grid gap-4 sm:grid-cols-2" role="status" aria-label="Loading your information">
		<Skeleton class="h-40 rounded-xl sm:col-span-2" />
		<Skeleton class="h-56 rounded-xl" />
		<Skeleton class="h-56 rounded-xl" />
	</div>
{:then pageData}
	{#if loadErrorSummary(pageData.loadErrors ?? [])}
		<LoadErrorBanner
			class="mb-4"
			message={loadErrorSummary(pageData.loadErrors ?? []) ??
				'Some of your information could not be loaded.'}
			onRetry={() => invalidateAll()}
		/>
	{/if}

	<PortalPage
		blocks={pageData.blocks}
		data={pageData.data}
		pageSlug={pageData.page.slug}
		pagination={pageData.pagination}
		{pageHref}
		{completing}
		{oncompletetask}
		{listingQuery}
		listingAccounts={pageData.listingAccounts}
		links={pageData.links}
		disabledActions={pageData.disabledActions}
		{onlistingchange}
		finish={page.data.branding?.finish ?? null}
	/>
{:catch}
	<!-- The streamed promise rejected outright (the loader threw, not a slice that failed and was
	     recorded in `loadErrors`). Without this branch Svelte re-throws into the nearest error
	     boundary and the whole shell — nav, header, sign-out — goes with it. -->
	<LoadErrorBanner
		message="Your information could not be loaded. Please try again shortly."
		onRetry={() => invalidateAll()}
	/>
{/await}
