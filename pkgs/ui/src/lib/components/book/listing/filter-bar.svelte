<script lang="ts">
	// The filter ROW: every filterable field, as a tag. A tag with a value names it and can be
	// cleared; a tag without one is the field, waiting, and has nothing to dismiss. Either opens
	// that field's editor — the bar does not carry the field's options.
	import XIcon from '@lucide/svelte/icons/x';
	import * as Popover from '../../ui/popover/index.js';
	import { appliedFilters } from './applied.js';
	import DateRangeFilter from './date-range-filter.svelte';
	import FilterField from './filter-field.svelte';
	import { FILTER_TAG_CLASS, FILTER_TAG_TRIGGER_CLASS } from './filters.js';
	import type { FilterDef } from './filter-panel.svelte';

	let {
		available = [],
		value = {},
		onchange,
		ondismiss,
		register
	}: {
		available?: readonly FilterDef[];
		value?: Record<string, string[]>;
		onchange: (next: Record<string, string[]>) => void;
		ondismiss: (key: string) => void;
		/** The toolbar dismisses from the funnel, which cannot see this editor. */
		register?: (discard: (key?: string) => void) => void;
	} = $props();

	const chips = $derived(appliedFilters(available, value));
	const chipOf = $derived(new Map(chips.map((chip) => [chip.key, chip])));

	let openKey = $state<string | null>(null);
	let openEditor = $state<{ discard: () => void } | undefined>(undefined);

	/** Drop a half-typed edit on the open tag before it unmounts. */
	export function discard(key?: string): void {
		if (key !== undefined && key !== openKey) return;
		openEditor?.discard();
	}

	$effect(() => {
		register?.(discard);
		return () => register?.(() => {});
	});

	function set(key: string, next: string[]): void {
		const out = { ...value };
		if (next.length) out[key] = next;
		else delete out[key];
		onchange(out);
	}
</script>

{#each available as filter (filter.key)}
	{@const chip = chipOf.get(filter.key)}
	<span class="{FILTER_TAG_CLASS}{chip ? '' : ' pr-1.5'}" data-slot="filter-tag">
		{#if filter.kind === 'daterange'}
			<DateRangeFilter
				variant="chip"
				label={filter.label}
				since={value[filter.key]?.[0] ?? ''}
				until={value[filter.key]?.[1] ?? ''}
				min={filter.min}
				max={filter.max}
				onchange={({ since, until }) => set(filter.key, since || until ? [since, until] : [])}
			/>
		{:else}
			<Popover.Root
				open={openKey === filter.key}
				onOpenChange={(next) => (openKey = next ? filter.key : null)}
			>
				<Popover.Trigger class={FILTER_TAG_TRIGGER_CLASS}>
					{#if chip}
						<span class="text-muted-foreground shrink-0">{chip.label}:</span>
						<span class="truncate font-medium" title={chip.text}>{chip.text}</span>
					{:else}
						<span class="text-muted-foreground">{filter.label}</span>
					{/if}
				</Popover.Trigger>
				<Popover.Content class={filter.kind === 'numberrange' ? 'w-72' : 'w-72 p-0'} align="start">
					{#if openKey === filter.key}
						<!-- Mounted only while open, so closing it flushes a half-typed range.
						     A dismiss discards first: that flush would put the key back. -->
						<FilterField
							bind:this={openEditor}
							{filter}
							value={value[filter.key] ?? []}
							onchange={(next) => set(filter.key, next)}
						/>
					{/if}
				</Popover.Content>
			</Popover.Root>
		{/if}
		{#if chip}
			<button
				type="button"
				class="hover:bg-muted-foreground/20 text-muted-foreground hover:text-foreground mr-1 shrink-0 cursor-pointer rounded-full p-0.5 transition-colors"
				aria-label="Clear {filter.label.toLowerCase()} filter"
				onclick={() => {
					// Close before the value drops, and discard first: unmounting the editor flushes,
					// which would put a half-typed amount back onto the field this click just cleared.
					discard(filter.key);
					if (openKey === filter.key) openKey = null;
					ondismiss(filter.key);
				}}
			>
				<XIcon class="size-3" aria-hidden="true" />
			</button>
		{/if}
	</span>
{/each}
