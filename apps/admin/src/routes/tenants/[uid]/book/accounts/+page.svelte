<script lang="ts">
	// Thin mount of the SHARED accounts listing (@graphene/ui book/accounts) over this section's
	// routes. No `newTransfer` builder: the affordance hides, keeping the surface read-only.
	//
	// `listing`/`matchCount`/`facets`/`onquery` put it in SERVER mode — the same mode the platform's
	// own /accounts page uses. Without them the component filtered whatever rows it had been given,
	// so an operator searching a firm's book was searching one batch and being told the answer for
	// the whole firm.
	import { AccountsListing, type AccountBookLinks } from '@graphene/ui';
	import { paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/book`);

	const links: AccountBookLinks = $derived({
		account: (uid) => `${base}/accounts/${uid}`,
		client: (uid) => `${base}/clients/${uid}`
	});

	/** The query lives in the URL, so the loader answers it and a search survives a reload.
	 *  `keepFocus` is why typing survives the navigation it triggers. */
	function navigate(next: ListingQuery): void {
		const url = new URL(page.url);
		url.search = paramsFromQuery(next).toString();
		goto(url, { keepFocus: true, noScroll: true, replaceState: true });
	}
</script>

<svelte:head><title>Accounts · {data.tenant.name} · Operations</title></svelte:head>

<AccountsListing
	accounts={data.accounts}
	listing={data.query}
	matchCount={data.matchCount}
	facets={data.facets}
	bounds={data.bounds}
	onquery={navigate}
	{links}
/>
