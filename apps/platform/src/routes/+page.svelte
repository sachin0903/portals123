<script lang="ts">
	// The firm dashboard — a thin mount of `@graphene/ui/book/dashboard`'s `FirmDashboard`, which
	// admin's operate-as-firm view mounts too, so the two are one screen. This page owns only the
	// navigation: filters and paging go through the URL, which re-runs the loader.
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { FirmDashboard, PLATFORM_DASH_LINKS } from '@graphene/ui/book/dashboard';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function navigate(next: ListingQuery) {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<FirmDashboard
	overview={data.overview}
	dash={data.dash}
	query={data.query}
	links={PLATFORM_DASH_LINKS}
	canReviews={data.canReviews}
	canJourneys={data.canJourneys}
	canManage={data.canManage}
	onquery={navigate}
	pageHref={(n) => pageHref(data.query, n, page.url.searchParams)}
	onretry={() => invalidateAll()}
/>
