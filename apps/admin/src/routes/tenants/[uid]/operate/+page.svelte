<script lang="ts">
	// The firm's dashboard, exactly as its own staff see it at `/` on the platform — the shared
	// `FirmDashboard`, with every link prefixed to this operate mount.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { FirmDashboard, prefixedDashLinks } from '@graphene/ui/book/dashboard';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { operateBase } from '$lib/record-focus';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const links = $derived(prefixedDashLinks(operateBase(data.tenant.uid)));

	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<svelte:head><title>Dashboard · {data.tenant.name}</title></svelte:head>

<FirmDashboard
	overview={data.overview}
	dash={data.dash}
	query={data.query}
	{links}
	canReviews
	canJourneys
	canManage={data.canManage}
	onquery={navigate}
	pageHref={(n) => pageHref(data.query, n, page.url.searchParams)}
	onretry={() => invalidateAll()}
/>
