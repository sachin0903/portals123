<script lang="ts">
	// A month grid you pick a range on: click the first day, click the last, and the days between
	// fill in. It replaces the pair of `<input type="date">` boxes the listings used to carry — a
	// native control, which the design language does not allow anywhere else, and which asked for
	// the two ends of a range as two unrelated questions. A grid answers "the last fortnight" in
	// two clicks and shows what was chosen; two text boxes never showed it at all.
	//
	// British defaults, because every firm on the platform is: weeks start on Monday and the
	// month/weekday names come from `en-GB`. `fixedWeeks` keeps the grid six rows tall, so the
	// popover does not change height as you page through the months.
	import { RangeCalendar as RangeCalendarPrimitive } from 'bits-ui';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import { cn } from '../../../utils.js';

	let {
		ref = $bindable(null),
		value = $bindable(),
		placeholder = $bindable(),
		class: className,
		...restProps
	}: RangeCalendarPrimitive.RootProps = $props();

	const NAV_BUTTON_CLASS =
		'text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring inline-flex size-7 items-center justify-center rounded-md transition-colors outline-none focus-visible:ring-2 disabled:pointer-events-none disabled:opacity-40';
</script>

<RangeCalendarPrimitive.Root
	bind:ref
	bind:value
	bind:placeholder
	locale="en-GB"
	weekStartsOn={1}
	weekdayFormat="short"
	fixedWeeks
	class={cn('w-fit select-none', className)}
	{...restProps}
>
	{#snippet children({ months, weekdays })}
		<RangeCalendarPrimitive.Header class="flex items-center justify-between pb-2">
			<RangeCalendarPrimitive.PrevButton class={NAV_BUTTON_CLASS}>
				<ChevronLeftIcon class="size-4" />
			</RangeCalendarPrimitive.PrevButton>
			<RangeCalendarPrimitive.Heading class="text-sm font-medium" />
			<RangeCalendarPrimitive.NextButton class={NAV_BUTTON_CLASS}>
				<ChevronRightIcon class="size-4" />
			</RangeCalendarPrimitive.NextButton>
		</RangeCalendarPrimitive.Header>

		<div class="flex flex-col gap-4 sm:flex-row">
			{#each months as month (month.value)}
				<RangeCalendarPrimitive.Grid class="w-full border-collapse">
					<RangeCalendarPrimitive.GridHead>
						<RangeCalendarPrimitive.GridRow class="flex">
							{#each weekdays as weekday (weekday)}
								<RangeCalendarPrimitive.HeadCell
									class="text-muted-foreground w-8 text-[0.7rem] font-normal"
								>
									<!-- `weekdayFormat="short"` gives "Mon"; one letter is all a grid this size
									     needs, and three ran the row wider than the days beneath it. -->
									{weekday.slice(0, 1)}
								</RangeCalendarPrimitive.HeadCell>
							{/each}
						</RangeCalendarPrimitive.GridRow>
					</RangeCalendarPrimitive.GridHead>
					<RangeCalendarPrimitive.GridBody>
						{#each month.weeks as week (week)}
							<RangeCalendarPrimitive.GridRow class="mt-1 flex w-full">
								{#each week as date (date)}
									<!-- The cell carries the range fill and the day carries the end caps, so the
									     band between two ends is unbroken: a rounded fill on every day would
									     read as a row of separate selections rather than one range. -->
									<RangeCalendarPrimitive.Cell
										{date}
										month={month.value}
										class="relative size-8 p-0 text-center text-xs data-selected:bg-primary/12 first:data-selected:rounded-l-md last:data-selected:rounded-r-md data-range-end:rounded-r-md data-range-start:rounded-l-md"
									>
										<RangeCalendarPrimitive.Day
											class="hover:bg-muted focus-visible:ring-ring data-selection-end:bg-primary data-selection-start:bg-primary data-selection-end:text-primary-foreground data-selection-start:text-primary-foreground data-disabled:text-muted-foreground/40 data-outside-month:text-muted-foreground/40 data-unavailable:text-muted-foreground/40 inline-flex size-8 items-center justify-center rounded-md font-normal transition-colors outline-none focus-visible:ring-2 data-disabled:pointer-events-none data-today:font-semibold data-unavailable:pointer-events-none data-unavailable:line-through"
										/>
									</RangeCalendarPrimitive.Cell>
								{/each}
							</RangeCalendarPrimitive.GridRow>
						{/each}
					</RangeCalendarPrimitive.GridBody>
				</RangeCalendarPrimitive.Grid>
			{/each}
		</div>
	{/snippet}
</RangeCalendarPrimitive.Root>
