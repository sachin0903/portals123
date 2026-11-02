<script lang="ts">
	// The host a real listing is: it owns the APPLIED filters as `$state`. A plain object passed
	// from a `.test.ts` cannot be `$state`, so nothing the toolbar writes would be reactive.
	import { untrack } from 'svelte';
	import ListingToolbar from './listing-toolbar.svelte';
	import type { FilterDef } from './filter-panel.svelte';

	let { available, initial = {} }: { available: FilterDef[]; initial?: Record<string, string[]> } =
		$props();
	let value = $state<Record<string, string[]>>(untrack(() => ({ ...initial })));
	let writeCount = 0;

	export function read(): Record<string, string[]> {
		return { ...$state.snapshot(value) };
	}

	/** How many times the listing has been told — a server-driven one makes each a navigation. */
	export function writes(): number {
		return writeCount;
	}

	/** What an empty-state Clear does: empties the values without going through the toolbar. */
	export function wipe(): void {
		value = {};
	}
</script>

<ListingToolbar
	filterDefs={available}
	filterValue={value}
	onfilters={(next) => {
		writeCount += 1;
		value = next;
	}}
/>
