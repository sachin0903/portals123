<script lang="ts">
	// A single-select listing filter with a built-in "all" option.
	//
	// Bits' Select has no concept of "no selection", so the unfiltered state needs a real
	// value: `ALL` is that sentinel. Pages compare against it via the exported constant
	// rather than a bare '' littered through their filter predicates.
	import * as Select from '../../ui/select/index.js';
	import { cn } from '../../../utils.js';
	import { FIELD_CLASS } from '../../../field-shell.js';
	import { ALL } from './filters.js';

	//
	// TWO MODES, matching ListingToolbar: `bind:value` for a locally-filtered listing, or
	// `value` + `onchange` for a server-driven one, where the selection lives in the URL and the
	// app navigates. A controlled Select cannot use `bind:` — the parent decides the next value.
	let {
		value = $bindable(ALL),
		options,
		allLabel,
		label,
		onchange,
		searchable = false,
		searchPlaceholder = 'Search…',
		class: className = 'h-9 min-w-[9rem]'
	}: {
		value?: string;
		/** Present ⇒ controlled: the choice is emitted, not written back. */
		onchange?: (next: string) => void;
		/** Selectable values in display order. The "all" entry is prepended here, not by callers.
		 *
		 *  `group` is optional and turns the list into labelled sections, in first-seen order — the
		 *  order of `options` still decides, so a caller sorts once and gets the same grouping every
		 *  render. Mixing grouped and ungrouped entries is allowed: the ungrouped ones render first,
		 *  under no heading, which is what a "recent"/"common" block at the top looks like. */
		options: { value: string; label: string; group?: string }[];
		/** Trigger text when nothing is filtered, e.g. "All statuses". */
		allLabel: string;
		/** Accessible name — the trigger shows only the current value, so this carries the meaning. */
		label: string;
		/** Adds a type-to-narrow box inside the dropdown. Off by default: most filter bars have a
		 *  handful of options and a search box on five entries is noise. Turn it on where the list is
		 *  genuinely long (the Events event-type picker has one option per operation in the estate,
		 *  which is scores of them) — a dropdown you have to scroll blind is not a filter. */
		searchable?: boolean;
		searchPlaceholder?: string;
		class?: string;
	} = $props();

	const labels = $derived(new Map(options.map((o) => [o.value, o.label])));

	// The narrowing text. Reset whenever the panel closes so re-opening always shows everything —
	// a filter box that remembers its last query silently hides options from the next reader.
	let search = $state('');
	let open = $state(false);
	$effect(() => {
		if (!open) search = '';
	});

	const needle = $derived(search.trim().toLowerCase());
	// Match the LABEL and the VALUE: the label is what a reader sees ("Created a client") while the
	// value is what they may have been given to look for (`client.created`, straight out
	// of a trail row or a support ticket). Searching only the label would make the raw id unfindable.
	const shown = $derived(
		needle
			? options.filter(
					(o) => o.label.toLowerCase().includes(needle) || o.value.toLowerCase().includes(needle)
				)
			: options
	);

	/** The shown options as [group, options] pairs, ungrouped first — see the `options` doc above. */
	const sections = $derived.by(() => {
		const out: { group: string | null; items: { value: string; label: string }[] }[] = [];
		for (const option of shown) {
			const group = option.group ?? null;
			const last = out.at(-1);
			if (last && last.group === group) last.items.push(option);
			else out.push({ group, items: [option] });
		}
		return out;
	});
</script>

<Select.Root
	type="single"
	{value}
	bind:open
	onValueChange={(next) => {
		if (onchange) onchange(next);
		else value = next;
	}}
>
	<Select.Trigger class={className} aria-label={label}>
		{value === ALL ? allLabel : (labels.get(value) ?? value)}
	</Select.Trigger>
	<Select.Content>
		{#if searchable}
			<!-- Inside the panel, above the items. `stopPropagation` on keydown because Select treats
			     typing as its own type-ahead and would steal every keystroke (and jump the highlight)
			     before the input saw it. -->
			<div class="p-1">
				<!-- `FIELD_CLASS`: its own copy had drifted to `text-sm` with no `text-base` under `md`,
				     which makes iOS Safari zoom the page on tap. -->
				<input
					bind:value={search}
					placeholder={searchPlaceholder}
					aria-label={searchPlaceholder}
					class={cn(FIELD_CLASS, 'placeholder:text-muted-foreground')}
					onkeydown={(e) => e.stopPropagation()}
				/>
			</div>
		{/if}
		<!-- The "all" entry is never filtered out: clearing the filter must stay reachable no matter
		     what has been typed. -->
		<Select.Item value={ALL} label={allLabel} />
		{#each sections as section, i (section.group ?? `ungrouped-${i}`)}
			{#if section.group}
				<Select.Group>
					<Select.GroupHeading>{section.group}</Select.GroupHeading>
					{#each section.items as option (option.value)}
						<Select.Item value={option.value} label={option.label} />
					{/each}
				</Select.Group>
			{:else}
				{#each section.items as option (option.value)}
					<Select.Item value={option.value} label={option.label} />
				{/each}
			{/if}
		{/each}
		{#if searchable && needle && shown.length === 0}
			<p class="text-muted-foreground px-2 py-1.5 text-sm">No matches.</p>
		{/if}
	</Select.Content>
</Select.Root>
