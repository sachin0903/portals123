<script lang="ts">
	// A compact date-range filter for a listing toolbar: one icon-only trigger (so it spends the
	// least width) that opens a RANGE CALENDAR in a popover. Shared so every listing's date filter
	// is the same control — the Events stream and the admin audit-log both use it, rather than each
	// hand-rolling its own date boxes that drift apart.
	//
	// It was a pair of `<input type="date">` boxes. Two problems, and the second is the reason this
	// changed: a native date input is a browser popup, which docs/design-language.md bans outright;
	// and two boxes ask for one range as two unrelated questions, so "the last fortnight" meant
	// reading a wall calendar, typing two dates and hoping they agreed. On the grid it is two
	// clicks, and the chosen span is named on the trigger. The presets sit above the grid in
	// three bands — a rolling window, the period so far, and the last completed period — and
	// the span is not repeated inside the panel, because the tag already says it.
	//
	// The API either side of this component is unchanged: `YYYY-MM-DD` strings in and out. The
	// calendar speaks `@internationalized/date` values, and `./date-range.ts` is the only place the
	// two meet — a caller that stores a range in the URL keeps storing plain strings.
	//
	// TWO MODES, matching ListingToolbar and FilterSelect: `bind:since`/`bind:until` for a listing
	// that filters an already-loaded array, or `since`/`until` + `onchange` for a server-driven one,
	// where the range lives in the URL and the app navigates. A controlled input cannot use `bind:` —
	// the parent decides the next value. The second mode is what lets the transactions ledger use
	// this control rather than the pair of raw date boxes it used to carry.
	import { getLocalTimeZone, today } from '@internationalized/date';
	import { Button } from '../../ui/button/index.js';
	import { cn } from '../../../utils.js';
	import { FIELD_CLASS } from '../../../field-shell.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import * as Popover from '../../ui/popover/index.js';
	import { RangeCalendar } from '../../ui/range-calendar/index.js';
	import { dateRangeLabel } from './applied.js';
	import { toCalendarDate, toDateText } from './date-range.js';
	import { DATE_PRESET_GROUPS, isDatePreset, matchingPreset, presetRange } from './date-presets.js';
	import { FILTER_TAG_TRIGGER_CLASS } from './filters.js';
	import type { DateRange } from 'bits-ui';
	import CalendarIcon from '@lucide/svelte/icons/calendar';

	let {
		since = $bindable(''),
		until = $bindable(''),
		/** Names the range in the accessible label / tooltip, e.g. "date range". */
		label = 'date range',
		/** Present ⇒ controlled: the range is emitted, not written back. Both bounds are sent every
		 *  time, because a server-driven caller turns them into ONE navigation and would otherwise
		 *  have to reconcile two. */
		onchange,
		/** Earliest date either bound may take (`YYYY-MM-DD`) — e.g. an account's opening date. */
		min = undefined,
		/** Latest date either bound may take (`YYYY-MM-DD`) — e.g. today, for a ledger of things
		 *  that have already happened. */
		max = undefined,
		/** `icon` — a square glyph, for a toolbar row where width is scarce. `field` — a full-width
		 *  control that reads like the selects beside it. `chip` — the tag in the filter row. */
		variant = 'icon'
	}: {
		since?: string;
		until?: string;
		label?: string;
		onchange?: (range: { since: string; until: string }) => void;
		min?: string;
		max?: string;
		variant?: 'icon' | 'field' | 'chip';
	} = $props();

	const active = $derived(!!(since || until));
	const heading = $derived(dateRangeLabel(since, until) || 'Any time');
	const now = $derived(today(getLocalTimeZone()));
	const matched = $derived(matchingPreset(since, until, now, { min, max }));
	const value = $derived<DateRange>({ start: toCalendarDate(since), end: toCalendarDate(until) });

	/** One writer for both modes, so the controlled path cannot forget a bound. */
	function set(next: { since: string; until: string }) {
		if (onchange) {
			onchange(next);
			return;
		}
		since = next.since;
		until = next.until;
	}

	function applyPreset(id: string) {
		if (!isDatePreset(id)) return;
		set(presetRange(id, now, { min, max }));
	}
</script>

<Popover.Root>
	<!-- The icon and field triggers compose `FIELD_CLASS`: the icon trigger's own copy of the shell
	     had drifted to a 2px full-opacity focus ring against the estate's tinted one. The chip is
	     the tag in the filter row, so it wears that tag's classes instead of a field shell. -->
	<Popover.Trigger
		class={cn(
			variant === 'chip'
				? FILTER_TAG_TRIGGER_CLASS
				: cn(
						FIELD_CLASS,
						'hover:bg-muted flex items-center',
						variant === 'field' ? 'h-9 w-full justify-between gap-2' : 'size-9 justify-center'
					)
		)}
		aria-label={active ? `${label}: ${heading}` : `Filter by ${label}`}
		title={active ? heading : `Filter by ${label}`}
	>
		{#if variant === 'chip'}
			{#if active}
				<span class="text-muted-foreground shrink-0">{label}:</span>
				<span class="truncate font-medium">{heading}</span>
			{:else}
				<span class="text-muted-foreground">{label}</span>
			{/if}
		{:else if variant === 'field'}
			<span class="flex min-w-0 items-center gap-2">
				<CalendarIcon
					class="size-4 shrink-0 {active ? 'text-primary' : 'text-muted-foreground'}"
					aria-hidden="true"
				/>
				<span class="truncate {active ? '' : 'text-muted-foreground'}"
					>{active ? heading : `Any ${label}`}</span
				>
			</span>
		{:else}
			<CalendarIcon class="size-4 {active ? 'text-primary' : 'text-muted-foreground'}" />
		{/if}
	</Popover.Trigger>
	<!-- `w-fit`: the preset menu was a fixed 20rem, wider than the month grid under it. The menu
	     matches its trigger, and the trigger fills this panel, so the panel has to be the grid. -->
	<Popover.Content class="w-fit" align="start">
		<!-- Above the grid, not beside a second copy of the span: the tag already names the dates.
		     Picking days that are not one of the presets leaves the menu on "Custom". -->
		<SelectMenu
			items={DATE_PRESET_GROUPS.map((group) => ({
				options: group.map((preset) => ({ value: preset.id, label: preset.label }))
			}))}
			dividers
			itemClass="text-xs"
			value={matched ?? ''}
			placeholder={active && !matched ? 'Custom' : 'Any time'}
			label="Date presets"
			size="sm"
			class="h-8 w-full cursor-pointer"
			onValueChange={applyPreset}
		/>
		<!-- `minValue`/`maxValue` are what the two boxes' cross-limits used to be: the grid simply
		     will not offer a day outside the allowed window, so a range that means nothing ("from
		     March, to January" — no rows, looks like missing data) cannot be picked at all. -->
		<RangeCalendar
			{value}
			minValue={toCalendarDate(min)}
			maxValue={toCalendarDate(max)}
			onValueChange={(next) =>
				set({ since: toDateText(next?.start), until: toDateText(next?.end) })}
		/>
		{#if active}
			<div class="flex justify-end pt-1">
				<Button variant="ghost" size="sm" onclick={() => set({ since: '', until: '' })}>
					Clear dates
				</Button>
			</div>
		{/if}
	</Popover.Content>
</Popover.Root>
