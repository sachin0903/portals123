<script lang="ts">
	// One value for the whole box or one per part, with a toggle: the common edit is "a bit more room",
	// one number, and four fields for that is four times the height in the rail. A box that already differs must open SPLIT, or the next edit overwrites parts the author never saw (`box-sides.ts::commonSide`).
	import UnitInput from '../unit-input/unit-input.svelte';
	import PropertyRow from '../property-row/property-row.svelte';
	import Segmented from '../segmented/segmented.svelte';
	import { cn } from '../../../utils.js';
	import {
		allSides,
		commonSide,
		describeSides,
		firstSide,
		SIDE_PARTS,
		sidesLinked,
		partGlyph,
		type BoxPart,
		type PartValues
	} from './box-sides.js';
	import SquareIcon from '@lucide/svelte/icons/square';
	import Maximize2Icon from '@lucide/svelte/icons/maximize-2';
	import PanelTopIcon from '@lucide/svelte/icons/panel-top';
	import PanelRightIcon from '@lucide/svelte/icons/panel-right';
	import PanelBottomIcon from '@lucide/svelte/icons/panel-bottom';
	import PanelLeftIcon from '@lucide/svelte/icons/panel-left';

	let {
		values,
		parts = SIDE_PARTS,
		units = [],
		disabled = false,
		min,
		idPrefix = 'box',
		label,
		set = false,
		class: className,
		onchange
	}: {
		/** Keyed by each part's `key`. Its key ORDER is the reading order — see `box-sides.ts`. */
		values: PartValues;
		parts?: readonly BoxPart[];
		units?: readonly string[];
		disabled?: boolean;
		min?: number;
		/** Two of these can share a panel (padding and margin), so the field ids must differ. */
		idPrefix?: string;
		/** THE PROPERTY'S OWN LABEL. Given, the control renders its whole inspector row, because only it
		 *  knows which mode it is in and the two modes want different rows: linked is one field beside the
		 *  label like every other property, split is a 2×2 grid needing the panel's full width with the
		 *  toggle up on the label line. A caller cannot decide that without owning the mode. */
		label?: string;
		/** The property carries a value — lights the label. `label` only. NO CLEAR: emptying a field is
		 *  the clear (an empty string drops the declaration), and a second glyph beside the linked/split
		 *  toggle read as a third mode of a control that has two. */
		set?: boolean;
		class?: string;
		/** Called with the WHOLE new set, so a linked edit is one change rather than N. */
		onchange: (values: PartValues) => void;
	} = $props();

	// Seeded from the values, then owned by the author: the effect only ever forces it OPEN, so the
	// re-render after a first keystroke cannot collapse a box just expanded. Called rather than inlined (the `MoneyInput` idiom: a prop read in a `$state` initialiser captures only its first value), and seeding it at all is what renders a mixed box split during SSR.
	const seedSplit = () => !sidesLinked(values);
	let split = $state(seedSplit());
	$effect(() => {
		if (!sidesLinked(values)) split = true;
	});

	const summary = $derived(describeSides(values));

	// A GLYPH, not the word: four labelled rows is four times the height, and "bottom" needs more width
	// than the number beside it. The panel icons draw a box with one edge weighted, which is the property.
	const SIDE_ICONS = {
		top: PanelTopIcon,
		right: PanelRightIcon,
		bottom: PanelBottomIcon,
		left: PanelLeftIcon
	} as const;

	const MODES = [
		{ value: 'all', label: 'One value for every side', icon: SquareIcon },
		{ value: 'split', label: 'A value per side', icon: Maximize2Icon }
	] as const;
</script>

<!-- The summary is the `title`, not a line of its own restating the fields below it — and none at all in linked mode, where the one field IS the value. -->
{#snippet modeToggle()}
	<!-- `ml-auto shrink-0` so a linked field takes what is left. Reversed (a fixed `w-28` field), the
	     149px control column in the style rail left the toggle under-width, and `Segmented`'s own
	     `overflow-x-auto` then put a scroll sliver beside a half-shown second icon. -->
	<Segmented
		class="ml-auto shrink-0"
		size="sm"
		shape="square"
		iconOnly
		{disabled}
		options={MODES}
		value={split ? 'split' : 'all'}
		onValueChange={(mode) => {
			const next = mode === 'split';
			// Collapsing a mixed box has to CHOOSE (`firstSide`), or the parts keep their differing values while the control claims to show one.
			if (!next && !sidesLinked(values)) onchange(allSides(values, firstSide(values)));
			split = next;
		}}
	/>
{/snippet}

{#snippet linkedField()}
	<UnitInput
		id={`${idPrefix}-all`}
		value={commonSide(values)}
		{units}
		{disabled}
		{min}
		placeholder="0"
		class="min-w-0 flex-1"
		onchange={(v) => onchange(allSides(values, v))}
	/>
{/snippet}

<!-- THE 2×2 GRID. Each field wears its side as a glyph INSIDE the box (`UnitInput`'s adornment), which
     is what buys the two columns the width to show their own numbers: a labelled column was four rows
     tall, and a labelled two-up left each column room for the unit and nothing else. -->
{#snippet splitGrid()}
	<div class="grid grid-cols-2 gap-1.5">
		{#each parts as part (part.key)}
			{@const glyph = partGlyph(part)}
			{@const Icon = glyph?.kind === 'side' ? SIDE_ICONS[glyph.side] : null}
			<UnitInput
				id={`${idPrefix}-${part.key}`}
				value={values[part.key] ?? ''}
				{units}
				{disabled}
				{min}
				placeholder="0"
				class="min-w-0"
				onchange={(v) => onchange({ ...values, [part.key]: v })}
			>
				{#snippet adornment()}
					<!-- The word stays as the accessible name: a glyph alone names nothing. -->
					{#if Icon}
						<Icon class="size-3.5" aria-label={part.label} />
					{:else if glyph?.kind === 'corner'}
						<span class="text-[0.6rem] font-semibold" aria-label={part.label}>{glyph.short}</span>
					{:else}
						<span class="text-[0.6rem] font-semibold uppercase" aria-label={part.label}>
							{part.label.slice(0, 2)}
						</span>
					{/if}
				{/snippet}
			</UnitInput>
		{/each}
	</div>
{/snippet}

{#if label === undefined}
	<!-- No label ⇒ the caller draws its own row and gets the controls alone (the mode toggle inline). -->
	<div class={cn('flex min-w-0 flex-col gap-1.5', className)} title={split ? summary : undefined}>
		{#if split}
			{@render splitGrid()}
			<div class="flex">{@render modeToggle()}</div>
		{:else}
			<div class="flex min-w-0 flex-nowrap items-center gap-1.5">
				{@render linkedField()}
				{@render modeToggle()}
			</div>
		{/if}
	</div>
{:else if split}
	<!-- SPLIT: the grid needs the panel's whole width, so the row stacks and the toggle rides the label
	     line — it answers the property, not any one field, so above the grid it read as a fifth control. -->
	<PropertyRow {label} {set} layout="stack" class={className} headerEnd={modeToggle}>
		<div title={summary}>{@render splitGrid()}</div>
	</PropertyRow>
{:else}
	<!-- LINKED: one field, so it sits beside its label like every other property in the panel. -->
	<PropertyRow {label} for={`${idPrefix}-all`} {set} class={className}>
		<div class="flex min-w-0 flex-nowrap items-center gap-1.5">
			{@render linkedField()}
			{@render modeToggle()}
		</div>
	</PropertyRow>
{/if}
