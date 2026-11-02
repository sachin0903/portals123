<script lang="ts">
	// Writes go through the model (`read.ts::write`/`writeParts`), never `component.addStyle()`: a
	// composite owns the whole shorthand, so writing round one doubles the edge.
	import ColorInput from '../../ui/color-input/color-input.svelte';
	import UnitInput from '../../ui/unit-input/unit-input.svelte';
	import BoxSides from '../../ui/box-sides/box-sides.svelte';
	import PropertyRow from '../../ui/property-row/property-row.svelte';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { Input } from '../../ui/input/index.js';
	import { cn } from '../../../utils.js';
	import * as Collapsible from '../../ui/collapsible/index.js';
	import { partValues, propertySet, write, writeParts, type PropertyView } from './read.js';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import AlignLeftIcon from '@lucide/svelte/icons/align-left';
	import AlignCenterIcon from '@lucide/svelte/icons/align-center';
	import AlignRightIcon from '@lucide/svelte/icons/align-right';
	import AlignJustifyIcon from '@lucide/svelte/icons/align-justify';
	import TypeIcon from '@lucide/svelte/icons/type';
	import ItalicIcon from '@lucide/svelte/icons/italic';
	import UnderlineIcon from '@lucide/svelte/icons/underline';
	import StrikethroughIcon from '@lucide/svelte/icons/strikethrough';
	// Self-import for the one-level recursion a mixed composite needs; `<svelte:self>` is deprecated.
	import PropertyControl from './property-control.svelte';

	let {
		view,
		brandColours = [],
		disabled = false,
		depth = 0,
		onchange
	}: {
		view: PropertyView;
		brandColours?: readonly string[];
		disabled?: boolean;
		depth?: number;
		/** Re-reads the models after a write; doing it synchronously stops a stale value for a frame. */
		onchange: () => void;
	} = $props();

	const id = $derived(`sp-${view.id}`);

	/** THE DEFAULT OF A FIELD MEASURED IN px IS A NUMBER. GrapesJS reports the CSS keyword — `medium`
	 *  for a font size, `normal` for a line height — and `medium px` beside `normal px` reads as a
	 *  mistake rather than as "this is what you get if you leave it alone". Keyed PER PROPERTY because
	 *  `normal` is 1.2 on a line height and 0 on a letter spacing; `auto` is left as the word, because a
	 *  width genuinely has no number until something decides one. Unmapped keywords stay as they are —
	 *  a guessed number is worse than the browser's own word for it. */
	const KEYWORD_DEFAULTS: Record<string, Record<string, string>> = {
		'font-size': {
			'xx-small': '9',
			'x-small': '10',
			small: '13',
			medium: '16',
			large: '18',
			'x-large': '24',
			'xx-large': '32'
		},
		'line-height': { normal: '1.2' },
		'letter-spacing': { normal: '0' },
		'word-spacing': { normal: '0' }
	};
	/** What an UNSET length shows as its placeholder. */
	const lengthDefault = $derived(KEYWORD_DEFAULTS[view.name]?.[view.value] ?? view.value ?? '');

	// Pictures keyed on the PROPERTY, not a label-length heuristic (`text-decoration` clipped mid-word at
	// the ~105px a choice gets in a 264px rail). An unmapped option keeps its label (the `?? {}` below).
	const CHOICE_ICONS: Record<string, Record<string, typeof AlignLeftIcon>> = {
		'text-align': {
			left: AlignLeftIcon,
			center: AlignCenterIcon,
			centre: AlignCenterIcon,
			right: AlignRightIcon,
			justify: AlignJustifyIcon
		},
		'font-style': { normal: TypeIcon, italic: ItalicIcon },
		'text-decoration': {
			none: TypeIcon,
			underline: UnderlineIcon,
			'line-through': StrikethroughIcon
		}
	};
	// The property NAME, not the id — a sub-property's id is prefixed (`border-left-style`).
	const icons = $derived(
		CHOICE_ICONS[Object.keys(CHOICE_ICONS).find((name) => view.name.endsWith(name)) ?? ''] ?? null
	);

	/**
	 * How wide a row of WORDS may get before the choice becomes a dropdown instead.
	 *
	 * NOT the heuristic the note above rejects. That one asked "should this be ICONS?", which a
	 * label length cannot answer — a picture has to mean something for its property, which is why
	 * `CHOICE_ICONS` is keyed by property and hand-written. This asks the far narrower question of
	 * whether the words FIT, and the remedy is a dropdown rather than a guessed picture.
	 *
	 * A choice gets around 105px of a 264px rail (the figure that note measured), and four words do
	 * not go in it. `Segmented` scrolls rather than clipping, which sounds like the safe failure and
	 * is not: a horizontal scrollbar inside a 105px control is close to invisible, so the last
	 * option reads as ABSENT rather than as off-screen. `font-weight` shipped exactly that — Light /
	 * Normal / Semi / Bold, with Bold entirely off the end of the rail.
	 *
	 * Counted in characters rather than measured, because measuring means doing it after layout and
	 * swapping the control under the pointer. ICONS are exempt: they are fixed-width, and the icon
	 * rows (align, font-style, decoration) are the ones that fit comfortably today.
	 */
	const CHOICE_LABEL_BUDGET = 18;
	const asDropdown = $derived(
		!icons &&
			view.control.kind === 'choice' &&
			view.control.options.reduce((sum, o) => sum + o.label.length, 0) > CHOICE_LABEL_BUDGET
	);

	/** `partial` is a value still being chosen — every pointer move of a colour drag. It reaches the
	 *  canvas and is NOT recorded (`read.ts::write`), so one gesture leaves one undo entry instead of
	 *  one per shade. The committed write at the end of the gesture is the recorded one. */
	function set(value: string, partial = false): void {
		write(view.property, value, partial ? { partial: true } : undefined);
		onchange();
	}

	// A group opens when it holds something. No border composite reaches this branch (`borders.ts` splices
	// the family out); seeding rather than an effect is what renders a set group open on the SERVER.
	const seedOpen = () => propertySet(view);
	let groupOpen = $state(seedOpen());
	$effect(() => {
		if (propertySet(view)) groupOpen = true;
	});
</script>

{#if view.control.kind === 'group'}
	<Collapsible.Root bind:open={groupOpen}>
		<div class={cn('flex flex-col', depth === 0 && 'py-1')}>
			<!-- The heading is the disclosure, lit when the group holds a value — the only cue while shut. -->
			<Collapsible.Trigger
				class={cn(
					'group/grp hover:text-foreground flex w-full cursor-pointer items-center gap-1 text-left text-xs font-medium tracking-wide transition-colors',
					// Tone, not hue — see `PropertyRow`'s note.
					propertySet(view) ? 'text-foreground' : 'text-muted-foreground'
				)}
			>
				<ChevronDownIcon
					class="size-3 shrink-0 transition-transform group-data-[state=closed]/grp:-rotate-90"
				/>
				{view.label}
			</Collapsible.Trigger>
			<Collapsible.Content>
				<div class="border-border/60 mt-0.5 ml-1 flex flex-col border-l pl-2">
					{#each view.subs as sub (sub.id)}
						<PropertyControl view={sub} {brandColours} {disabled} depth={depth + 1} {onchange} />
					{/each}
				</div>
			</Collapsible.Content>
		</div>
	</Collapsible.Root>
{:else if view.control.kind === 'parts'}
	<!-- OUTSIDE a `PropertyRow`: only the box knows which mode it is in, and the two modes want
	     different rows — linked is one field beside its label, split a full-width grid with the toggle
	     up on the label line. -->
	<BoxSides
		idPrefix={id}
		label={view.label}
		{disabled}
		set={view.set}
		values={partValues(view)}
		parts={view.control.parts}
		units={view.control.units}
		min={view.control.min}
		onchange={(values) => {
			// Synchronous, so backbone-undo fuses the four writes into ONE undo step; never await here.
			writeParts(view, values);
			onchange();
		}}
	/>
{:else}
	<!-- NO CLEAR AFFORDANCE. Every control can already say "unset" in its own words: a field is
	     emptied (an empty string drops the declaration — `read.ts::write`), a dropdown offers Default
	     as an option, a Segmented's neutral member IS the default, and `ColorInput` carries its own
	     clear. A × beside each of twenty rows was a second way to do what the control does. -->
	<PropertyRow label={view.label} for={id} set={view.set}>
		{#if view.control.kind === 'colour'}
			<ColorInput
				{id}
				{disabled}
				value={view.value}
				swatches={brandColours}
				oninput={(v) => set(v, true)}
				onchange={(v) => set(v)}
			/>
		{:else if view.control.kind === 'length'}
			<!-- AN UNSET PROPERTY SHOWS ITS DEFAULT AS A PLACEHOLDER, not as a value: `getValue()` falls
			     back to the default (`read.ts::PropertyView.value`), so `medium`, `normal` and `auto` were
			     printed as if the author had typed them and a set property looked identical to an unset one.
			     Muted, it reads as what it is — what you get if you leave this alone. -->
			<UnitInput
				{id}
				{disabled}
				value={view.set ? view.value : ''}
				placeholder={lengthDefault || 'auto'}
				units={view.control.units}
				min={view.control.min}
				onchange={set}
			/>
		{:else if view.control.kind === 'slider'}
			<!-- No Slider primitive yet, and `opacity` is the only slider GrapesJS declares: a plain number. -->
			<UnitInput
				{id}
				{disabled}
				value={view.value}
				units={[]}
				min={view.control.min}
				placeholder={String(view.control.max)}
				onchange={set}
			/>
		{:else if view.control.kind === 'choice' && asDropdown}
			<!-- Too many words for the rail — see CHOICE_LABEL_BUDGET. Deliberately NOT offering
			     "Default" the way a declared `select` does: a radio's neutral member IS its default
			     (`font-weight: normal`), so a second way to say the same thing would be two answers
			     for one question. -->
			<SelectMenu
				{id}
				{disabled}
				value={view.value}
				items={view.control.options.map((o) => ({ value: o.value, label: o.label }))}
				size="sm"
				onValueChange={set}
			/>
		{:else if view.control.kind === 'choice'}
			<Segmented
				size="sm"
				shape="square"
				iconOnly={!!icons}
				{disabled}
				value={view.value}
				onValueChange={set}
				options={view.control.options.map((o) => ({
					value: o.value,
					// Kept behind an icon for the accessible name; capitalised — the preset's casing varies.
					label: o.label.charAt(0).toUpperCase() + o.label.slice(1),
					...(icons?.[o.value] ? { icon: icons[o.value] } : {})
				}))}
			/>
		{:else if view.control.kind === 'select'}
			<!-- DEFAULT IS AN OPTION, not a × beside the row: unsetting a property is one of the answers
			     the question has, so it belongs in the list of answers. `''` is what `write` clears on. -->
			<SelectMenu
				{id}
				{disabled}
				value={view.value}
				items={[
					{ value: '', label: 'Default' },
					...view.control.options.map((o) => ({ value: o.value, label: o.label }))
				]}
				size="sm"
				onValueChange={set}
			/>
		{:else}
			<!-- The `read.ts::controlFor` fallback: an unmapped type stays visible and editable. Its default
			     is a placeholder for the reason the length field above gives. -->
			<Input
				{id}
				{disabled}
				class="h-8"
				value={view.set ? view.value : ''}
				placeholder={view.value}
				spellcheck="false"
				oninput={(e) => set(e.currentTarget.value)}
			/>
		{/if}
	</PropertyRow>
{/if}
