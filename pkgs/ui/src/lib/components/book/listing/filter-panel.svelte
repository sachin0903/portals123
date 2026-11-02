<script lang="ts">
	// The filter PANEL, behind the toolbar funnel. Applied as you go but DEBOUNCED, so "Save
	// filters" saves nothing — it shuts the panel and gives the rows back.
	import type { MultiSelectOption } from '../../ui/multi-select/types.js';
	import FilterChoices from './filter-choices.svelte';
	import FilterRange from './filter-range.svelte';
	import DateRangeFilter from './date-range-filter.svelte';
	import { Button } from '../../ui/button/index.js';
	import InspectorSector from '../../ui/inspector-sector/inspector-sector.svelte';
	import { untrack } from 'svelte';
	import { sameFilters } from './applied.js';

	/** `options` is the loader's facets, not the rows on screen, so a value paged in from behind
	 *  the window is still pickable. */
	export type FilterDef = {
		key: string;
		label: string;
		options: MultiSelectOption[];
		searchPlaceholder?: string;
		single?: boolean;
		allLabel?: string;
		/** Option LABEL in mono, hint in plain — for a filter whose label IS an identifier. */
		monoLabel?: boolean;
		/** Type to search. The list stays hidden until the query has something in it, apart from
		 *  values already chosen, which stay so they can be cleared. */
		autocomplete?: boolean;
		/** `daterange` ⇒ `[since, until]` in `YYYY-MM-DD`; `numberrange` ⇒ `[from, to]`. */
		kind?: 'values' | 'daterange' | 'numberrange';
		currency?: string;
		unit?: string;
		/** The calendar's window (`YYYY-MM-DD`), or the ends the number slider draws its track over. */
		min?: string;
		max?: string;
	};

	let {
		available = [],
		value = {},
		onsave,
		onclose,
		debounce = 300
	}: {
		available?: FilterDef[];
		value?: Record<string, string[]>;
		onsave: (next: Record<string, string[]>) => void;
		onclose?: () => void;
		debounce?: number;
	} = $props();

	let pending = $state<Record<string, string[]>>(untrack(() => ({ ...value })));
	/** Plain, not `$state`: the effect below must react to `value` changing, never to this. */
	let sent = untrack(() => ({ ...value }));
	let timer: ReturnType<typeof setTimeout> | undefined;

	/** DEBOUNCED: a server-driven listing turns every call into a navigation, so applying per
	 *  keystroke made typing "3000000" seven loader runs and seven round trips. */
	function commit(next: Record<string, string[]>): void {
		pending = next;
		clearTimeout(timer);
		timer = setTimeout(flush, debounce);
	}

	function flush(): void {
		clearTimeout(timer);
		if (sameFilters(pending, sent)) return;
		sent = { ...pending };
		onsave(pending);
	}

	/** Re-seed only on a change from SOMEWHERE ELSE (a chip cleared, a browser Back): our own write
	 *  echoes back here, and re-seeding on it throws away whatever was typed since. */
	$effect(() => {
		if (sameFilters(value, sent)) return;
		sent = { ...value };
		pending = { ...value };
	});

	$effect(() => () => flush());

	const isSet = (key: string) => (pending[key]?.length ?? 0) > 0;

	/** A RANGE is one fact about two bounds, so it counts as one however many ends are set. */
	function chosenCount(filter: FilterDef): number {
		const held = pending[filter.key] ?? [];
		if (!held.length) return 0;
		return filter.kind === 'daterange' || filter.kind === 'numberrange' ? 1 : held.length;
	}

	function set(key: string, next: string[]): void {
		const out = { ...pending };
		if (next.length) out[key] = next;
		else delete out[key];
		commit(out);
	}

	function reset(): void {
		// Immediate, not debounced: a timer on Reset reads as a dead button.
		clearTimeout(timer);
		pending = {};
		sent = {};
		onsave({});
	}
</script>

<div class="flex max-h-[min(30rem,65vh)] flex-col">
	<div class="flex min-h-0 flex-1 flex-col overflow-y-auto px-1.5 py-1">
		{#each available as filter (filter.key)}
			<InspectorSector label={filter.label} set={isSet(filter.key)}>
				{#snippet mark()}
					{#if chosenCount(filter) > 1}
						<span class="text-primary text-[0.7rem] font-semibold tabular-nums"
							>{chosenCount(filter)}</span
						>
					{:else}
						<span class="bg-primary/60 size-1.5 rounded-full"></span>
					{/if}
				{/snippet}
				<div class="flex flex-col gap-1.5 px-2 pt-1 pb-2">
					{#if filter.kind === 'numberrange'}
						<FilterRange
							label={filter.label}
							value={pending[filter.key] ?? []}
							min={filter.min}
							max={filter.max}
							currency={filter.currency}
							onchange={(next) => set(filter.key, next)}
						/>
					{:else if filter.kind === 'daterange'}
						<DateRangeFilter
							variant="field"
							label={filter.label.toLowerCase()}
							since={pending[filter.key]?.[0] ?? ''}
							until={pending[filter.key]?.[1] ?? ''}
							min={filter.min}
							max={filter.max}
							onchange={({ since, until }) => set(filter.key, since || until ? [since, until] : [])}
						/>
					{:else}
						<FilterChoices
							options={filter.options}
							value={pending[filter.key] ?? []}
							single={filter.single}
							allLabel={filter.allLabel}
							label={filter.label}
							searchPlaceholder={filter.searchPlaceholder}
							autocomplete={filter.autocomplete}
							onchange={(next) => set(filter.key, next)}
						/>
					{/if}
				</div>
			</InspectorSector>
		{/each}

		{#if !available.length}
			<p class="text-muted-foreground text-xs">This listing has nothing to filter on.</p>
		{/if}
	</div>

	<div
		class="border-border/60 flex shrink-0 items-center justify-between gap-2 border-t px-3 py-2.5"
	>
		<!-- Everything is applied by now, so this only SHUTS the panel — and is never disabled. -->
		<Button
			size="sm"
			onclick={() => {
				flush();
				onclose?.();
			}}>Save filters</Button
		>
		{#if Object.keys(pending).length}
			<Button variant="ghost" size="sm" class="text-muted-foreground" onclick={reset}>Reset</Button>
		{/if}
	</div>
</div>
