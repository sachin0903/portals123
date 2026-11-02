<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import {
		ReviewsListing,
		reviewActionOptions,
		type ReviewsListingFilters
	} from '@graphene/ui/book/reviews';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	// The Action filter offers what is actually on the queue — book reviews and held provider
	// actions alike — so a kind that can be held is never missing from the menu.
	const actionOptions = $derived(reviewActionOptions(data.reviews));

	const base = $derived(`/tenants/${data.tenant.uid}/operate`);

	function applyFilters(next: ReviewsListingFilters): void {
		const url = new URL(page.url);
		url.searchParams.delete('scope');
		url.searchParams.delete('submitted_by');
		for (const id of next.submitted_by) url.searchParams.append('submitted_by', id);
		if (next.action_key) url.searchParams.set('action', next.action_key);
		else url.searchParams.delete('action');
		if (next.status) url.searchParams.set('status', next.status);
		else url.searchParams.delete('status');
		if (next.q.trim()) url.searchParams.set('q', next.q.trim());
		else url.searchParams.delete('q');
		if (next.seat) url.searchParams.set('seat', next.seat);
		else url.searchParams.delete('seat');
		goto(`${url.pathname}${url.search}`, { keepFocus: true, noScroll: true });
	}

	function applyOpen(uid: string | null): void {
		const url = new URL(page.url);
		if (uid) url.searchParams.set('open', uid);
		else url.searchParams.delete('open');
		goto(`${url.pathname}${url.search}`, { keepFocus: true, noScroll: true });
	}
</script>

<svelte:head><title>Reviews · {data.tenant.name}</title></svelte:head>

<ReviewsListing
	reviews={data.reviews}
	loadError={data.loadError}
	filters={data.filters}
	{actionOptions}
	openUid={data.openUid}
	links={{
		client: (uid) => `${base}/clients/${uid}`,
		account: (uid) => `${base}/accounts/${uid}`
	}}
	onfilters={applyFilters}
	onopen={applyOpen}
	onretry={() => invalidate('app:reviews')}
	actions={{
		enhance,
		...(data.canApprove ? { approve: '?/approve', reject: '?/reject' } : {}),
		cancel: '?/cancel'
	}}
/>
