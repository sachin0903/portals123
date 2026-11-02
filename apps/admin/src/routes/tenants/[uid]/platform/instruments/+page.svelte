<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import InstrumentsSection from '$lib/catalogue/instruments-section.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	/** Put the search in the URL and let the loader answer it. `keepFocus` is why typing survives
	 *  the navigation it triggers; `replaceState` keeps a search out of the back button, since
	 *  every keystroke would otherwise be a history entry. */
	function search(q: string): void {
		const next = new URL(page.url);
		if (q) next.searchParams.set('q', q);
		else next.searchParams.delete('q');
		goto(next, { keepFocus: true, noScroll: true, replaceState: true });
	}

	/**
	 * A filter change is a real navigation, unlike a keystroke: it is one deliberate act, so it
	 * earns a history entry and the back button widens the list again. An empty value clears the
	 * key rather than writing a blank one, which is what keeps an unfiltered universe at a clean
	 * URL. The WHOLE SET arrives in one call: two `goto`s in a row race and the first key is lost.
	 */
	function filter(values: Record<string, string[]>): void {
		const next = new URL(page.url);
		for (const [key, list] of Object.entries(values)) {
			next.searchParams.delete(key);
			for (const value of list) if (value) next.searchParams.append(key, value);
		}
		goto(next, { noScroll: true });
	}

	/** Clear every narrowing at once, the search included — what the toolbar's Clear does. */
	function clear(): void {
		const next = new URL(page.url);
		for (const key of ['q', 'asset_type', 'currency', 'exchange_code'])
			next.searchParams.delete(key);
		goto(next, { noScroll: true });
	}
</script>

<InstrumentsSection
	instruments={data.instruments}
	total={data.instrumentsTotal}
	query={data.query}
	filters={data.filters}
	facets={data.facets}
	onsearch={search}
	onfilter={filter}
	onclear={clear}
	canManage={data.canManage}
	{form}
/>
