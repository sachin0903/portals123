<script lang="ts">
	import { Calendar, Chart } from 'layerchart/html';
	import 'layerchart/shadcn-svelte.css';
	import Segmented from '../ui/segmented/segmented.svelte';

	export type ActivityDay = {
		date: string;
		actions: number;
		page_views: number;
	};

	let { days = [] }: { days?: ActivityDay[] } = $props();

	const options = [
		{ value: 'actions', label: 'Actions' },
		{ value: 'page_views', label: 'Page views' },
		{ value: 'both', label: 'Both' }
	] as const;

	let mode = $state<(typeof options)[number]['value']>('both');

	function parseDay(iso: string): Date {
		const parts = iso.split('-').map(Number);
		return new Date(parts[0] ?? 1970, (parts[1] ?? 1) - 1, parts[2] ?? 1);
	}

	function metric(day: ActivityDay): number {
		if (mode === 'actions') return day.actions;
		if (mode === 'page_views') return day.page_views;
		return day.actions + day.page_views;
	}

	const points = $derived(days.map((day) => ({ date: parseDay(day.date), value: metric(day) })));
	const byTime = $derived(new Map(points.map((point) => [point.date.getTime(), point.value])));
	const max = $derived(points.reduce((peak, point) => Math.max(peak, point.value), 0));
	const start = $derived(points[0]?.date ?? new Date());
	const end = $derived.by(() => {
		const last = points.at(-1)?.date ?? start;
		return new Date(last.getFullYear(), last.getMonth(), last.getDate() + 1);
	});

	/** The five shades, quietest first. The cells and the legend both read from here. */
	const SHADES = [
		'var(--muted)',
		'color-mix(in oklab, var(--brand) 35%, var(--muted))',
		'color-mix(in oklab, var(--brand) 55%, var(--muted))',
		'color-mix(in oklab, var(--brand) 75%, var(--muted))',
		'var(--brand)'
	] as const;
	/** Where each non-zero band begins, as a share of the busiest day. */
	const STOPS = [0, 0.25, 0.5, 0.75] as const;

	function colour(value: number): string {
		if (max <= 0 || value <= 0) return SHADES[0];
		const step = value / max;
		if (step < 0.25) return SHADES[1];
		if (step < 0.5) return SHADES[2];
		if (step < 0.75) return SHADES[3];
		return SHADES[4];
	}

	/**
	 * The legend's swatches, one per shade, each saying which counts it covers.
	 *
	 * Derived from the same thresholds as `colour`, so the key cannot drift from the cells —
	 * the hand-written legend had four swatches for five shades and no values at all, so
	 * "Less → More" was the whole explanation. The title is what the reader gets on hover.
	 */
	const legend = $derived.by(() => {
		const noun = (n: number) =>
			mode === 'actions'
				? n === 1
					? 'action'
					: 'actions'
				: mode === 'page_views'
					? n === 1
						? 'page view'
						: 'page views'
					: n === 1
						? 'event'
						: 'events';
		const bands = STOPS.map((stop, i) => {
			const next = STOPS[i + 1];
			const low = Math.max(1, Math.ceil(stop * max));
			const high = next === undefined ? max : Math.max(low, Math.ceil(next * max) - 1);
			const range = low === high ? `${low}` : `${low}–${high}`;
			return {
				colour: SHADES[i + 1],
				title: max <= 0 ? 'No activity' : `${range} ${noun(high)} in a day`
			};
		});
		return [{ colour: SHADES[0], title: 'No activity' }, ...bands];
	});

	/** Local midnight, so a day is a day across the DST change. */
	const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
	/** The Sunday on or before `d` — the same week boundary layerchart's Calendar columns use. */
	const weekStart = (d: Date) => {
		const at = day(d);
		at.setDate(at.getDate() - at.getDay());
		return at;
	};
	/** Which column the Calendar puts `d` in. Rounded, since a DST week is not 168 hours. */
	const weekIndex = (d: Date) =>
		Math.round((weekStart(d).getTime() - weekStart(start).getTime()) / 604_800_000);

	/**
	 * The month labels along the top, thinned to what fits.
	 *
	 * layerchart writes one 12px label per month at the month's column. On a wide card a column is
	 * eleven pixels and a four-week month has room for "Sept"; in a narrower card — a team member's
	 * profile sits beside its sidebar — a column is six or seven pixels and the labels ran into
	 * one another. So the Calendar's own labels are off, and these are placed by the same rule but
	 * a label is dropped when it would start inside the one before it.
	 */
	function monthLabels(cellWidth: number): { x: number; label: string }[] {
		const labels: { x: number; label: string }[] = [];
		const fmt = new Intl.DateTimeFormat('en-GB', { month: 'short' });
		const minGap = 30; // room for the widest short month name at 12px
		let lastX = -Infinity;
		const first = new Date(start.getFullYear(), start.getMonth(), 1);
		if (first < day(start)) first.setMonth(first.getMonth() + 1);
		for (
			let month = first;
			month < end;
			month = new Date(month.getFullYear(), month.getMonth() + 1, 1)
		) {
			// A month begins at the first full column on or after its first day.
			const sunday = weekStart(month);
			if (sunday < day(month)) sunday.setDate(sunday.getDate() + 7);
			const x = weekIndex(sunday) * cellWidth;
			if (x - lastX < minGap) continue;
			labels.push({ x, label: fmt.format(month) });
			lastX = x;
		}
		return labels;
	}

	const byDate = $derived(new Map(days.map((d) => [parseDay(d.date).getTime(), d])));

	const longDate = new Intl.DateTimeFormat('en-GB', {
		weekday: 'short',
		day: 'numeric',
		month: 'short',
		year: 'numeric'
	});

	function count(n: number, one: string, many: string): string {
		return `${n} ${n === 1 ? one : many}`;
	}

	/** What a day says on hover: the date, then its actions and page views. */
	function detail(date: Date): { when: string; lines: string[] } {
		const found = byDate.get(date.getTime());
		const actions = found?.actions ?? 0;
		const views = found?.page_views ?? 0;
		const lines =
			actions + views === 0
				? ['No activity']
				: [count(actions, 'action', 'actions'), count(views, 'page view', 'page views')];
		return { when: longDate.format(date), lines };
	}

	function caption(date: Date, value: number): string {
		const when = new Intl.DateTimeFormat('en-GB', {
			day: 'numeric',
			month: 'short',
			year: 'numeric'
		}).format(date);
		return `${when}: ${value}`;
	}

	/**
	 * The hovered day. One card for the whole grid, shown on `pointerenter` with no delay — the
	 * native `title` waits about a second before it appears, which on a 7px square read as
	 * "there is no hover". The card hugs the nearest edge in the outer columns so it is not
	 * clipped.
	 */
	let hovered = $state<{ x: number; y: number; width: number; date: Date } | null>(null);
	const columns = $derived(weekIndex(end) + 1);
	/**
	 * Column width, copied out of the chart once it has measured. The month names are drawn
	 * above the chart, in the page, because a label positioned from the plot climbs out of it
	 * and onto the filter.
	 */
	let cellWidth = $state(0);

	function rememberWidth(width: number) {
		if (width > 0 && Math.abs(width - cellWidth) > 0.5) {
			queueMicrotask(() => {
				cellWidth = width;
			});
		}
	}

	function anchor(x: number, width: number): string {
		const col = Math.round(x / Math.max(width, 1));
		if (col < 6) return '0';
		if (col > columns - 7) return '-100%';
		return '-50%';
	}
</script>

<div class="space-y-4">
	<Segmented {options} bind:value={mode} size="sm" label="Activity shown" />
	{#if points.length > 0}
		<div class="flex flex-col gap-3">
			<div class="relative h-4">
				{#each cellWidth > 0 ? monthLabels(cellWidth) : [] as month (`${month.x}-${month.label}`)}
					<span
						class="text-muted-foreground absolute bottom-0 text-xs leading-none whitespace-nowrap"
						style:left="{month.x}px"
					>
						{month.label}
					</span>
				{/each}
			</div>
			<div class="h-[130px] w-full">
				<Chart
					data={points}
					x="date"
					c="value"
					cRange={['var(--muted)', 'var(--brand)']}
					padding={{ top: 0 }}
				>
					<Calendar {start} {end} monthLabel={false}>
						{#snippet children({ cells, cellSize })}
							{rememberWidth(cellSize[0])}
							{#each cells as cell (cell.data.date?.toISOString?.() ?? `${cell.x}-${cell.y}`)}
								{@const value = byTime.get(cell.data.date?.getTime?.() ?? 0) ?? 0}
								<div
									class="rounded-[3px]"
									style:position="absolute"
									style:left="{cell.x + 1}px"
									style:top="{cell.y + 1}px"
									style:width="{Math.max(cellSize[0] - 2, 1)}px"
									style:height="{Math.max(cellSize[1] - 2, 1)}px"
									style:background={colour(value)}
									aria-label={caption(cell.data.date, value)}
									role="img"
									data-day
									onpointerenter={() =>
										(hovered = {
											x: cell.x,
											y: cell.y,
											width: cellSize[0],
											date: cell.data.date
										})}
									onpointerleave={() => (hovered = null)}
								></div>
							{/each}
							{#if hovered}
								{@const info = detail(hovered.date)}
								<div
									class="bg-foreground text-background pointer-events-none absolute z-50 rounded-md px-2.5 py-1.5 text-xs whitespace-nowrap shadow-md"
									style:left="{hovered.x +
										(anchor(hovered.x, hovered.width) === '-50%'
											? hovered.width / 2
											: anchor(hovered.x, hovered.width) === '0'
												? 0
												: hovered.width)}px"
									style:top="{hovered.y - 4}px"
									style:transform="translate({anchor(hovered.x, hovered.width)}, -100%)"
									role="tooltip"
								>
									<div class="font-medium">{info.when}</div>
									{#each info.lines as line (line)}
										<div class="opacity-80">{line}</div>
									{/each}
								</div>
							{/if}
						{/snippet}
					</Calendar>
				</Chart>
			</div>
		</div>
		<div class="text-muted-foreground flex items-center justify-end gap-1.5 text-xs">
			<span>Less</span>
			{#each legend as band, i (i)}
				<span
					class="size-3 rounded-[3px]"
					style:background={band.colour}
					title={band.title}
					aria-label={band.title}
					role="img"
				></span>
			{/each}
			<span>More</span>
		</div>
	{:else if days.length === 0}
		<p class="text-muted-foreground text-sm">No activity in the last year.</p>
	{/if}
</div>
