<script lang="ts" generics="T">
	// The shared listing table: column definitions in, sorted rows out, cells rendered by
	// the caller's snippet. Every listing in the portals should use this so sort affordances,
	// alignment, density and the empty state behave identically everywhere.
	//
	// Sorting is client-side and deliberate: these listings are a firm's book (hundreds of
	// rows, already fetched), not a paged server query. If a listing outgrows that, sort
	// moves to the server and `sort` becomes the bound query parameter — the column API
	// doesn't change.
	import ArrowDownIcon from '@lucide/svelte/icons/arrow-down';
	import ArrowUpIcon from '@lucide/svelte/icons/arrow-up';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronsUpDownIcon from '@lucide/svelte/icons/chevrons-up-down';
	import FolderIcon from '@lucide/svelte/icons/folder';
	import FolderOpenIcon from '@lucide/svelte/icons/folder-open';
	import type { Snippet } from 'svelte';
	import * as Table from '../table/index.js';
	import { Skeleton } from '../skeleton/index.js';
	import { cn } from '../../../utils.js';
	import {
		groupRows,
		type DataTableGroupKey,
		type DataTableGroupRow,
		type DataTableRenderRow
	} from './group.js';
	import { hideClasses } from './hide.js';
	import { defaultSort, nextSort, sortRows } from './sort.js';
	import type { DataTableColumn, DataTableSort } from './types.js';

	let {
		columns,
		rows,
		getKey,
		cell,
		footer,
		sort = $bindable(null),
		empty = 'No rows.',
		/** Tighter rows. Off by default so every table shares one row height. */
		dense = false,
		/** Accessible name for the table ("Holdings", "Accounts") — every listing should carry one. */
		label,
		/**
		 * Disable the sortable headers — for when `rows` is one page of a server-paginated set,
		 * where a client-side sort would silently sort the page and lie about the whole list.
		 * Headers keep their text but lose the affordance, and explain themselves via `title`.
		 */
		sortDisabled = false,
		/**
		 * Server-side sort. Supply this for a paginated listing and the headers stay live: the
		 * click updates `sort` and calls back so the loader can re-fetch the whole set in the new
		 * order. Without it a paginated table's only honest option is `sortDisabled`, because
		 * sorting one page client-side sorts the page and lies about the list.
		 */
		onSort,
		/**
		 * Make rows activatable. The row becomes a keyboard-reachable button-like element (Enter
		 * and Space), NOT an anchor — a listing whose rows navigate should still pass a real href
		 * in a cell for middle-click and "open in new tab", which this cannot provide.
		 */
		onRowClick,
		rowClass,
		/**
		 * Optional second row under a data row — a field-level diff, a nested table, a decision
		 * panel. The listing still owns when a row is open (`isExpanded`); this only renders the
		 * extra row so every expandable table keeps the same sort, density and empty-state as the
		 * rest of the book instead of dropping back to a raw `<table>`.
		 */
		detail,
		isExpanded,
		/**
		 * Scroll the ROWS inside the table instead of the page: the wrapper takes the height its
		 * parent gives it and the header sticks to the top of it. For a listing that fills a screen —
		 * the toolbar and the pager stay put and reachable however long the list is, which is the whole
		 * reason to do it.
		 *
		 * The wrapper takes `min-h-0 flex-1`, not `h-full`: it is a direct flex child of the card's
		 * content column, and `h-full` there resolves against a height the parent has not settled yet.
		 * `min-h-0` is the load-bearing half — a flex child refuses to shrink below its content
		 * without it, so the table would simply grow and nothing would scroll.
		 *
		 * On a narrow viewport the PAGE scrolls, not this wrapper: a nested Y scroller inside a
		 * phone-sized pane is what trapped readers behind a table that would not move, and
		 * `table-fixed` percentage columns then overlapped their nowrap cells. Inner Y-scroll and
		 * the sticky header start at `md`. `maxHeight` is the exception — a bound in viewport
		 * units (the client portal's uncapped blocks) still has to clip or the masonry cell grows
		 * without limit.
		 *
		 * Given an unbounded parent this is a no-op rather than a bug: `flex-1` outside a flex
		 * container does nothing.
		 */
		scroll = false,
		/**
		 * An ABSOLUTE bound for the scroll wrapper, as a Tailwind max-height class — for a table whose
		 * parent gives it no height to take.
		 *
		 * `scroll` alone is a flex trick, so it needs an ancestor with a settled height. In the client
		 * portal's masonry there is none by construction: the cell is sized FROM the card, so a card
		 * that asked for its cell's height would close a feedback loop (`portal-page.svelte` records
		 * the 147,000px one). A bound in VIEWPORT units breaks that circularity — it is not a
		 * percentage of anything the grid is about to compute, so the cell simply measures a child
		 * that has stopped growing.
		 */
		maxHeight = undefined,
		/**
		 * `table-layout: fixed`, with each column's share declared in its own `headerClass` (`w-[22%]`).
		 *
		 * Use it whenever the column widths matter. Under the default `auto` layout the browser hands
		 * the leftover width out IN PROPORTION TO CONTENT, so the two widest columns take most of it —
		 * a Client and an Adviser column end up four times the width of Accounts and the row reads as
		 * unevenly spaced however the cells are capped. Capping cannot fix that: a ceiling stops a
		 * column growing past a limit, it does not stop it claiming slack below one.
		 *
		 * Fixed layout also makes truncation reliable, because a cell's width no longer depends on what
		 * is in it. The same pairing the audit tables already use (`table-fixed` + `w-[…]` heads).
		 *
		 * Only from `md`: below that the table keeps `auto` layout and `min-w-full`, so columns take
		 * the room their nowrap cells need and the wrapper scrolls sideways instead of overlapping.
		 */
		fixed = false,
		/**
		 * The rows are being REPLACED: draw skeleton rows in the body and leave the header, the frame
		 * and everything around the table where they are. For a table that `Streamed` holds on screen
		 * (`keep`) while the next page arrives — the reader sees the page turning without the tiles
		 * and toolbar around it going grey. As many bars as there were rows, so the box keeps its
		 * height and the pager beneath does not jump.
		 */
		loading = false,
		/** Group rows into collapsible bands: a row's path, outermost first, or `[]` for none. */
		groupBy,
		/** Paths of the shut bands. Replace it, never mutate it: `$state` does not proxy a `Set`. */
		collapsed = $bindable(new Set()),
		/** A control before each band's label (a "select this folder" box), on the `lead` ruler. */
		groupLead,
		class: className
	}: {
		columns: DataTableColumn<T>[];
		rows: T[];
		getKey: (row: T) => string | number;
		cell: Snippet<[T, DataTableColumn<T>]>;
		/**
		 * A summary row beneath the data — a total, a count, an in-transit figure.
		 *
		 * Inside THIS table, as a real `<tfoot>`, which is the whole point: a second `<table>`
		 * stacked underneath sizes its own columns from its own content, so the total lands under
		 * the wrong headings and the figures stop lining up with the money above them.
		 *
		 * It sits outside the sorted set by construction, so a total stays a total whatever order
		 * the reader puts the rows in. The snippet is handed the columns so it can space itself
		 * across them; it renders nothing when there are no rows, since a total of nothing is
		 * noise on top of the empty state.
		 */
		footer?: Snippet<[DataTableColumn<T>[]]>;
		sort?: DataTableSort | null;
		/**
		 * What the table says when it has no rows — a sentence, or a snippet for the cases that need
		 * an icon and a way out.
		 *
		 * A STRING COULD NOT FOLLOW THE LAW. This is the most-used empty surface in the product (35
		 * mounts across the three portals and this package), and while `empty` was `string` no
		 * caller could attach the action that fixes the emptiness — so the most common "nothing
		 * here" in the estate was structurally incapable of saying what to do about it. A snippet
		 * takes `EmptyState`, which is where that belongs.
		 *
		 * THE DEFAULT NAMES NO KIND ON PURPOSE. It was "Nothing to show.", which sounds like an
		 * answer and is not one: it cannot tell "you have none yet" from "none match your filter"
		 * from "we could not read them". "No rows." is deliberately flat — it describes the table,
		 * claims nothing about the reader's book, and reads as unfinished, because it is. Pass the
		 * sentence that fits the kind.
		 *
		 * (An earlier version of this comment said 71 mounts, 37 of them defaulted. Both were wrong:
		 * `grep '<DataTable'` also matches `$state<DataTableSort | null>` and the other generic type
		 * arguments, which is 36 of that 71. Parse the opening tag, or count `import`s, before
		 * quoting a figure like this in a comment that outlives the measurement.)
		 */
		empty?: string | Snippet;
		dense?: boolean;
		label?: string;
		sortDisabled?: boolean;
		onSort?: (sort: DataTableSort | null) => void;
		onRowClick?: (row: T) => void;
		rowClass?: (row: T) => string | undefined;
		detail?: Snippet<[T]>;
		isExpanded?: (row: T) => boolean;
		scroll?: boolean;
		/** Tailwind max-height class; only meaningful with `scroll`. */
		maxHeight?: string;
		fixed?: boolean;
		loading?: boolean;
		groupBy?: (row: T) => DataTableGroupKey[];
		collapsed?: Set<string>;
		groupLead?: Snippet<[DataTableGroupRow<T>]>;
		class?: string;
	} = $props();

	/** Skeleton rows while `loading`: one per row on screen, or a short page when there were none. */
	const SKELETON_ROWS_WHEN_EMPTY = 8;
	const skeletonRows = $derived(
		Array.from({ length: rows.length || SKELETON_ROWS_WHEN_EMPTY }, (_, i) => i)
	);

	const SORT_DISABLED_TITLE = 'Sorting the full list isn’t available yet.';

	// Rows arrive already ordered when the server is doing the sorting, so re-sorting the page
	// locally would reorder within the page and fight the server's ordering.
	const serverSorted = $derived(onSort !== undefined);
	const presorted = $derived(sortDisabled || serverSorted);
	/** What the table is ordered by: the `sortDefault` fallback only when this table owns the sort. */
	const effectiveSort = $derived(sort ?? (presorted ? null : defaultSort(columns)));
	const sorted = $derived(presorted ? rows : sortRows(rows, columns, effectiveSort));

	const renderRows = $derived<DataTableRenderRow<T>[]>(
		groupBy
			? groupRows(rows, groupBy, columns, effectiveSort, collapsed, presorted)
			: sorted.map((row) => ({ kind: 'row', row, depth: 0 }))
	);

	function toggleGroup(path: string): void {
		// Reassign (Svelte tracks that), keeping the caller's Set class so a `SvelteSet` stays one.
		const Ctor = (collapsed.constructor ?? Set) as SetConstructor;
		const next = new Ctor(collapsed);
		if (!next.delete(path)) next.add(path);
		collapsed = next;
	}

	// Inline style: a computed `pl-[…]` class has no Tailwind rule. `base` adds a plain cell's `px-2`.
	const step = (depth: number, base = 0) =>
		depth > 0 ? `padding-left: ${base + depth * 1.25}rem` : undefined;

	const indentIndex = $derived(
		Math.max(
			0,
			columns.findIndex((c) => c.indent)
		)
	);
	/** First column of the merged lead cell, or -1: the `lead` column when it precedes the indent. */
	const leadIndex = $derived.by(() => {
		const i = columns.findIndex((c) => c.lead === true);
		return i >= 0 && i <= indentIndex ? i : -1;
	});
	const merged = $derived(leadIndex >= 0 ? columns.slice(leadIndex, indentIndex + 1) : []);

	function toggle(column: DataTableColumn<T>) {
		if (!column.sortable || (sortDisabled && !serverSorted)) return;
		// `effectiveSort`, not `sort`: reading `sort` (null on the fallback column) made the first
		// click restart that column instead of reversing the direction already showing.
		sort = nextSort(effectiveSort, column);
		onSort?.(sort);
	}

	// A row-level handler must not eat the controls inside the row. A listing with a switch, a
	// link or a menu in a cell would otherwise fire BOTH the control and the row activation from
	// one click, and the row usually navigates away — losing the toggle the user actually aimed at.
	function activate(row: T, event: Event): void {
		if (!onRowClick) return;
		const target = event.target as HTMLElement | null;
		// Skip clicks that landed on a control INSIDE the row (a link, a Done button) — those own
		// the click. `!== event.currentTarget` is the whole fix: the row itself carries
		// `role="button"` (below), so a bare `closest()` matched the row on every click and this
		// guard swallowed all of them. `onRowClick` never fired on any DataTable — the
		// contributions work queue looked inert, and so did the admin API log.
		const interactive = target?.closest(
			'a, button, input, select, textarea, label, [role="button"]'
		);
		if (interactive && interactive !== event.currentTarget) return;
		// A deliberate text selection is not a click on the row.
		if (window.getSelection()?.toString()) return;
		onRowClick(row);
	}
</script>

<Table.Root
	class={cn(fixed && 'md:table-fixed', className)}
	aria-label={label}
	aria-busy={loading || undefined}
	containerClass={scroll
		? maxHeight
			? cn('min-h-0 overflow-y-auto', maxHeight)
			: 'md:min-h-0 md:flex-1 md:overflow-y-auto'
		: undefined}
>
	<!-- Sticky only when scrolling: a header pinned inside a wrapper that does not scroll is just a
	     header, but it still creates a stacking context, and that is enough to put it over a dropdown
	     panel opened from the toolbar above. `bg-card` rather than transparent, because rows scroll
	     UNDER it — and the tables sit on glass panels, so the ground has to be painted or the text
	     reads through. On mobile the page is the scroller, so the header is not sticky there unless
	     a `maxHeight` bound made this wrapper the scrollport. -->
	<Table.Header
		class={scroll
			? cn('bg-card', maxHeight ? 'sticky top-0 z-10' : 'md:sticky md:top-0 md:z-10')
			: undefined}
	>
		<Table.Row>
			{#each columns as column (column.key)}
				{@const active = (serverSorted || !sortDisabled) && effectiveSort?.key === column.key}
				<Table.Head
					class={cn(
						column.align === 'right' && 'text-right',
						column.align === 'center' && 'text-center',
						hideClasses(column),
						column.headerClass
					)}
					style={column.headerStyle}
					aria-sort={active
						? effectiveSort?.dir === 'asc'
							? 'ascending'
							: 'descending'
						: undefined}
				>
					{#if column.sortable && sortDisabled && !serverSorted}
						<!-- The header stays a (non-focusable, explained) element rather than plain text so
						     the layout doesn't jump when a listing flips between sortable and paginated. -->
						<span
							aria-disabled="true"
							title={SORT_DISABLED_TITLE}
							class={cn(
								'-mx-1 inline-flex cursor-default items-center gap-1 px-1 py-0.5',
								column.align === 'right' && 'flex-row-reverse'
							)}
						>
							{#if column.head}
								{@render column.head(column)}
							{:else}
								{column.header}
							{/if}
						</span>
					{:else if column.sortable}
						<button
							type="button"
							onclick={() => toggle(column)}
							class={cn(
								'hover:text-foreground group -mx-1 inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors',
								'focus-visible:ring-ring focus-visible:ring-2 focus-visible:outline-none',
								column.align === 'right' && 'flex-row-reverse',
								active && 'font-semibold'
							)}
						>
							{#if column.align === 'center'}
								<!-- Balances the chevron, so the label centres on the cells' axis. -->
								<span class="size-3.5 shrink-0"></span>
							{/if}
							{#if column.head}
								{@render column.head(column)}
							{:else}
								<span>{column.header}</span>
							{/if}
							{#if !active}
								<!-- DIMMED, never invisible. At `opacity-0` a sortable column was
								     indistinguishable from a fixed one until the pointer happened to land on
								     it, so "which columns can I sort by" was a question you answered by
								     sweeping the header row — and on touch, not at all. The documents tables
								     already showed a dimmed chevron and their test says the affordance must
								     not be hover-only; this is the shared table catching up. -->
								<ChevronsUpDownIcon
									class="size-3.5 shrink-0 opacity-30 transition-opacity group-hover:opacity-70 group-focus-visible:opacity-70"
								/>
							{:else if effectiveSort?.dir === 'asc'}
								<ArrowUpIcon class="size-3.5 shrink-0" />
							{:else}
								<ArrowDownIcon class="size-3.5 shrink-0" />
							{/if}
						</button>
					{:else if column.head}
						{@render column.head(column)}
					{:else}
						{column.header}
					{/if}
				</Table.Head>
			{/each}
		</Table.Row>
	</Table.Header>
	<Table.Body>
		{#if loading}
			<!-- The previous page's rows are gone and the next page's are not here yet: bars in the
			     same cells, so the columns keep their widths and the reader sees WHERE the rows will land. -->
			{#each skeletonRows as i (i)}
				<Table.Row class="hover:bg-transparent" aria-hidden="true">
					{#each columns as column (column.key)}
						<Table.Cell class={cn(dense && 'py-1.5', hideClasses(column), column.cellClass)}>
							<Skeleton
								class={cn(
									'h-4 w-3/5',
									column.align === 'right' && 'ml-auto',
									column.align === 'center' && 'mx-auto'
								)}
							/>
						</Table.Cell>
					{/each}
				</Table.Row>
			{/each}
		{:else if renderRows.length === 0}
			<Table.Row class="hover:bg-transparent">
				<Table.Cell
					colspan={columns.length}
					class={cn(
						'text-muted-foreground text-center text-sm',
						// A snippet brings its own box (`EmptyState` has padding of its own), so the cell
						// only supplies the vertical room a sentence needs.
						typeof empty === 'string' ? 'py-10' : 'py-6'
					)}
				>
					{#if typeof empty === 'string'}
						{empty}
					{:else}
						{@render empty()}
					{/if}
				</Table.Cell>
			</Table.Row>
		{:else}
			{#each renderRows as entry (entry.kind === 'group' ? `g:${entry.path}` : `r:${getKey(entry.row)}`)}
				{#if entry.kind === 'group'}
					{@const Chevron = entry.collapsed ? ChevronRightIcon : ChevronDownIcon}
					{@const BandIcon = entry.icon ?? (entry.collapsed ? FolderIcon : FolderOpenIcon)}
					<Table.Row
						class="bg-muted/25 hover:bg-muted/40"
						aria-level={entry.depth + 1}
						aria-posinset={entry.position}
						aria-setsize={entry.setSize}
					>
						<Table.Cell
							colspan={columns.length}
							class={cn(dense && 'py-1.5', leadIndex >= 0 && columns[leadIndex]?.cellClass)}
						>
							<div class="flex min-w-0 items-center gap-2" style={step(entry.depth)}>
								{#if groupLead}
									<div class="flex shrink-0 items-center">{@render groupLead(entry)}</div>
								{/if}
								<!-- The label carries the unit, or a bare count reads as part of the name. -->
								<button
									type="button"
									class="flex min-w-0 flex-1 items-center gap-2 py-0.5 text-left font-medium"
									aria-expanded={!entry.collapsed}
									aria-label={`${entry.label}, ${entry.count} ${entry.count === 1 ? 'row' : 'rows'}`}
									onclick={() => toggleGroup(entry.path)}
								>
									<Chevron class="text-muted-foreground size-4 shrink-0" />
									<BandIcon class="text-primary size-4 shrink-0" />
									<span class="truncate">{entry.label}</span>
									<span class="text-muted-foreground text-xs font-normal">{entry.count}</span>
								</button>
							</div>
						</Table.Cell>
					</Table.Row>
				{:else}
					{@const row = entry.row}
					{@const expanded = isExpanded?.(row) ?? false}
					<Table.Row
						class={cn(onRowClick && 'hover:bg-muted/50 cursor-pointer', rowClass?.(row))}
						role={onRowClick ? 'button' : undefined}
						tabindex={onRowClick ? 0 : undefined}
						aria-expanded={detail ? expanded : undefined}
						onclick={onRowClick ? (event) => activate(row, event) : undefined}
						onkeydown={onRowClick
							? (event) => {
									// A key on a control inside the row belongs to that control.
									if (event.target !== event.currentTarget) return;
									if (event.key === 'Enter' || event.key === ' ') {
										// Space scrolls the page by default, which is wrong once the row is
										// the control.
										event.preventDefault();
										activate(row, event);
									}
								}
							: undefined}
					>
						{#each columns as column, i (column.key)}
							{#if i === leadIndex}
								<!-- `pr-2` restores the right padding a checkbox cell otherwise drops. -->
								<Table.Cell
									colspan={merged.length}
									class={cn(
										dense && 'py-1.5',
										...merged.map((c) => c.cellClass),
										'[&:has([role=checkbox])]:pr-2'
									)}
								>
									<div class="flex min-w-0 items-center gap-2" style={step(entry.depth)}>
										{#each merged as part, j (part.key)}
											<div
												class={j === merged.length - 1
													? 'min-w-0 flex-1'
													: 'flex shrink-0 items-center'}
											>
												{@render cell(row, part)}
											</div>
										{/each}
									</div>
								</Table.Cell>
							{:else if leadIndex < 0 || i < leadIndex || i > indentIndex}
								<Table.Cell
									class={cn(
										dense && 'py-1.5',
										column.align === 'right' && 'text-right',
										column.align === 'center' && 'text-center',
										hideClasses(column),
										column.cellClass
									)}
									style={i === indentIndex ? step(entry.depth, 0.5) : undefined}
								>
									{@render cell(row, column)}
								</Table.Cell>
							{/if}
						{/each}
					</Table.Row>
					{#if detail && expanded}
						<Table.Row class="hover:bg-transparent">
							<Table.Cell colspan={columns.length} class="bg-muted/30 py-4">
								{@render detail(row)}
							</Table.Cell>
						</Table.Row>
					{/if}
				{/if}
			{/each}
		{/if}
	</Table.Body>
	{#if footer && renderRows.length > 0 && !loading}
		<Table.Footer>
			{@render footer(columns)}
		</Table.Footer>
	{/if}
</Table.Root>
