<script lang="ts">
	// The listing pager: the shared `Pagination` control plus the range it is showing.
	//
	// It composes `ui/pagination` rather than reimplementing it — that component already does
	// numbered pages, sibling truncation and an `href` LINK MODE, and link mode is exactly right
	// here: these listings keep their query in the URL, so pages are real links that can be
	// shared, opened in a new tab and walked with the back button.
	//
	// What this adds is the offset↔page translation (the API pages by offset, the control counts
	// pages) and the "1–50 of 1,340" label, which is the part a pager is actually read for.
	import { pageOf, rangeLabel, type ListingQuery } from './query.js';
	import PaginationStrip from './pagination-strip.svelte';

	let {
		query,
		total,
		shown,
		href,
		exportHref
	}: {
		query: ListingQuery;
		/** Matches across the whole book, from the server. */
		total: number;
		/** Rows on this page — the label needs it, since the last page is short. */
		shown: number;
		/** Builds the URL for a 1-indexed page. Given, the pages render as anchors. */
		href?: (page: number) => string;
		/** Export the whole filtered set. Absent ⇒ no menu.
		 *
		 *  Here rather than in the toolbar because this strip IS the bottom of the table's card. It
		 *  therefore inherits the strip's `total > 0` guard, which is right: an export of nothing is
		 *  a button that hands you a file with one header row in it. */
		exportHref?: string;
	} = $props();

	const pageCount = $derived(Math.max(1, Math.ceil(total / Math.max(1, query.limit))));
	const current = $derived(pageOf(query));
</script>

<!-- A bordered strip across the foot of the card, page numbers on the right and the range sitting
     quietly on the left. It reads as part of the card's furniture
     rather than a line of text floating under a table, and — because these listings scroll their rows
     internally now — a strip with its own top border is what marks where the scrolling stops.
     The RANGE always shows; the page CONTROLS only past one page. Hiding the whole thing on a single
     page (the old behaviour) meant that with a 25-row page and a book of sixteen, the pager was
     invisible on every listing in the product, so a short list was indistinguishable from the first
     page of a long one. "16 results" answers that in two words; numbered controls for one page are
     what would actually be noise, and those still hide.
     `-mx-4` bleeds the strip out through CardContent's own `px-4` so the top border reaches both
     card edges — inside that padding it would float as a short line with gaps either side. -->
<!-- Nothing at all for an empty set. A pager over zero rows has nothing to page and nothing worth
     counting, and "No matches" under an empty table repeats the empty state inside it — on the
     contributions queue it was worse than redundant: that pager counts the BOOKED page only, so it
     read "No matches" beneath a list full of detected credits. The table's own empty state is the one
     place that message belongs. -->
{#if total > 0}
	<PaginationStrip
		label={rangeLabel(query, total, shown)}
		page={current}
		{pageCount}
		{href}
		{exportHref}
	/>
{/if}
