<script lang="ts">
	// The contributions workbench — the whole surface, as one mountable component.
	//
	// ONE QUEUE, no tabs. A credit the custodian has reported and a contribution already moving
	// through Morgan Lloyd are both "a payment that needs an adviser to know about it", so they
	// share a list: detected credits appear at status `identified` (Detected), which is the
	// stage 0051 declared and nothing had ever produced. Splitting them across tabs meant the
	// count you needed was always on the tab you were not looking at.
	//
	// Relief-at-source and reconciliation are not rendered as views here. Both are real and
	// computed server-side — the panel shows a contribution's own position on each — but neither
	// has enough behind it to justify a page yet: SEI reclaims relief in a monthly batch we hold
	// no identifier for, and a reconciliation table over a ledger with no matched credits is a
	// heading above nothing.
	//
	// The portal waterfall: data in as typed props, mutations as injected callbacks. Pass no
	// callbacks and the whole thing is read-only, which is how the admin portal mounts it.
	// Only the pager: the search/filter row belongs to WorkQueue, which already had one.
	import {
		PaginationStrip,
		pageOf,
		rangeLabelFromOffset,
		type ListingQuery
	} from '../listing/index.js';
	import { cn } from '../../../utils.js';
	import { panelSlide } from '../../../panel-motion.js';
	import ClassifyPanel from './classify-panel.svelte';
	import KpiStrip from './kpi-strip.svelte';
	import WorkQueue from './work-queue.svelte';
	import { tick, untrack } from 'svelte';
	import {
		detectedOnPage,
		isDetected,
		resolveFocus,
		transactionIdOf,
		workQueue
	} from './detected.js';
	import type {
		Contribution,
		ContributionLinks,
		StageSummary,
		UnclassifiedCredit
	} from './types.js';

	/** The mutations. Each is optional; an absent one hides its control rather than failing. */
	export interface ContributionActions {
		/** Saves the classification AND records it with the scheme administrator. There is no
		 *  separate `record`: they were two presses for one decision, with a `confirmed` state
		 *  between them that badges as done while nobody downstream has been told. The chaining is
		 *  the mounting app's (`apps/platform/.../contributions/+page.server.ts`), so the two API
		 *  endpoints stay separate and a failed vendor call cannot roll back a correct
		 *  classification. */
		classify?: (uid: string, patch: Record<string, unknown>) => void;
		lodge?: (uid: string) => void;
		cancel?: (uid: string, reason: string) => void;
		/** Classify a credit the feed detected — creates the contribution against the money. */
		classifyCredit?: (transactionId: number, patch: Record<string, unknown>) => void;
		refreshFromMorganLloyd?: (uid: string) => void;
	}

	let {
		contributions,
		credits = [],
		summary = null,
		focus = null,
		busy = false,
		links,
		actions,
		listing = null,
		matchCount = null,
		facets,
		onquery,
		pageHref
	}: {
		contributions: Contribution[];
		credits?: UnclassifiedCredit[];
		/** WHOLE-BOOK stage totals from the API. Passed straight to the KPI strip, which falls back
		 *  to the rows on screen when it is absent — see `stage_totals`. */
		summary?: StageSummary | null;
		/** Deep-link target from the Action required strips on Transactions/Accounts/Clients. */
		focus?: string | null;
		busy?: boolean;
		/** Injected navigation, per the portal waterfall — the workbench hardcodes no hrefs. Absent
		 *  ⇒ client names render as plain text, which is how a mount with no client route (an
		 *  older admin read-only overlay) stays correct rather than dead-linking. */
		links?: ContributionLinks;
		actions?: ContributionActions;
		/** SERVER-DRIVEN when supplied: `contributions` is one page and the API applied the search
		 *  and filters. The detected credits are NOT paged — they are the front of the queue and
		 *  the work an adviser came here for, so they are always all shown above the page. */
		listing?: ListingQuery | null;
		matchCount?: number | null;
		facets?: Record<string, string[]>;
		onquery?: (next: ListingQuery) => void;
		/** Builds the URL for a 1-indexed page, so the pager renders real links (shareable,
		 *  middle-clickable, back-button friendly). Injected because only the app knows its own
		 *  route and which unrelated params must survive — the same reason `links` is injected. */
		pageHref?: (page: number) => string;
	} = $props();

	const server = $derived(!!listing && !!onquery);
	// Seeded from the deep link, so a page that arrives on `?focus=` renders with the panel already
	// open rather than pushing it in after hydration. Through `resolveFocus`, the same as the effect
	// below, so a transaction uid from an Action required strip opens its detected row on first
	// paint too. The effect follows later navigations.
	let selectedUid = $state<string | null>(
		untrack(() => resolveFocus(focus, workQueue(contributions, credits), credits))
	);

	const rows = $derived(workQueue(contributions, credits));
	const selected = $derived(rows.find((c) => c.uid === selectedUid) ?? null);

	/** THE PAGER COUNTS WHAT IS ON SCREEN. The API's `total` is the booked rows only, and the
	 *  queue puts the detected credits above them — so "16 results" sat under a list of twenty-one,
	 *  and a page of pure detected credits read "No matches". The detected rows on THIS page come
	 *  from the same function the queue draws them with (`detectedOnPage`), so the two cannot
	 *  disagree: they are counted into the total, into the first page's range, and offset the
	 *  ranges of every page after it. The PAGE COUNT stays the booked rows' — the detected ones are
	 *  all on page one, so they add no page. */
	const pageRange = $derived.by(() => {
		if (!server || !listing || matchCount === null) return null;
		// Those matching the query, wherever the reader is paged to: they lead the total on every
		// page, and shift every later page's range by the rows they took on the first.
		const detected = detectedOnPage(rows, { ...listing, offset: 0 }).length;
		const total = matchCount + detected;
		if (total === 0) return null;
		const first = listing.offset === 0;
		const shown = contributions.length + (first ? detected : 0);
		const offset = first ? 0 : listing.offset + detected;
		return {
			label: rangeLabelFromOffset(offset, shown, total),
			page: pageOf(listing),
			pageCount: Math.max(1, Math.ceil(matchCount / Math.max(1, listing.limit)))
		};
	});
	// Apply the deep link once per navigation. The status filter it might be hidden behind is the
	// QUERY's now (server-side, in the URL), so there is nothing local to clear — a focus that
	// lands on a filtered-out row selects it and the queue shows the filtered set, which is the
	// same behaviour every other book listing has when arriving on a deep link.
	let appliedFocus = $state<string | null>(null);
	$effect(() => {
		if (!focus || focus === appliedFocus) return;
		appliedFocus = focus;
		// A transaction uid from an Action required strip is translated to the detected row it
		// names — see `resolveFocus`. Read inside the effect so the rows in hand are the ones
		// the page mounted with; the effect still runs once per `focus`.
		selectedUid = resolveFocus(
			focus,
			untrack(() => rows),
			untrack(() => credits)
		);
	});

	/** Saving a detected row creates the contribution; saving a real one amends it. Routed here
	 *  rather than in the panel, so the panel stays a control surface that knows nothing about
	 *  which endpoint its row came from. */
	function save(uid: string, patch: Record<string, unknown>) {
		const transactionId = transactionIdOf(uid);
		if (transactionId === null) actions?.classify?.(uid, patch);
		else actions?.classifyCredit?.(transactionId, patch);
	}

	/** A detected row has no contribution behind it, so only classification applies to it. */
	const canSave = $derived(
		!!selected && (isDetected(selected.uid) ? !!actions?.classifyCredit : !!actions?.classify)
	);

	let root = $state<HTMLElement | null>(null);

	function close(): void {
		selectedUid = null;
	}

	// Opening the panel narrows the queue and folds its columns, so the row that was clicked can
	// reflow out of the scrolled window. Bring it back once layout has settled.
	$effect(() => {
		if (!selectedUid) return;
		void tick().then(() =>
			requestAnimationFrame(() =>
				root?.querySelector('tr.contribution-selected')?.scrollIntoView({ block: 'nearest' })
			)
		);
	});

	/** Esc closes the panel, unless it belongs to something else first: a popover, menu or dialog
	 *  layered on top, or a field being typed in (the cancel reason lives in the panel, and losing
	 *  it to a stray Esc would lose what was typed). */
	function onkeydown(event: KeyboardEvent): void {
		if (event.key !== 'Escape' || !selectedUid || event.defaultPrevented) return;
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
		if (
			document.querySelector(
				'[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"]'
			)
		) {
			return;
		}
		close();
	}
</script>

<svelte:window {onkeydown} />

<!-- ONE SCREEN, the same frame the book listings use: a flex column the height of the viewport under
     the top bar, with the QUEUE as the only thing that scrolls. `-m-4 md:-m-6` cancels the shell's
     canvas padding and re-applies it inside, because a page that owns the full height cannot also be
     inset by its parent; `min-h-0` is what lets a flex child shrink below its content, without which
     the list grows and nothing scrolls. -->
<div
	bind:this={root}
	class="-m-4 flex min-w-0 flex-col gap-4 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<!-- The topbar crumb names the page. The KPI strip below is the live tally — a heading that
	     restated "N in progress" was a second count of the same strip. -->

	<!-- The KPI strip renders even with nothing in the book: a workbench showing only a sentence
	     reads as a failed load, where a strip of zeros reads as "no work yet".
	     THE SENTENCE ITSELF IS GONE FROM HERE. It was an Alert saying "No contributions yet" above a
	     table saying "No contributions match your filters" — two answers to one question, and the
	     lower one blamed a filter nobody had set. The work queue's own empty state carries it now,
	     where the rows would be. -->

	<!-- NO PIPELINE STRIP. It was a second row of the same counts, clickable as a status filter —
	     so the page carried two answers to "how much is at each stage" (which had already drifted:
	     it grouped `ras_applied` with `ras_received`, the KPI strip did not) and two places to
	     narrow by one. The strip's job as a CONTROL moved into the queue's own toolbar, beside the
	     filters it belongs with; its job as a READOUT is the KPI strip's, which now states the
	     money rather than a row count. -->
	<div class="shrink-0">
		<KpiStrip {rows} {summary} />
	</div>

	<!-- THE PANEL OPENS ON DEMAND. It used to hold a fixed 420px column whether or not anything was
	     selected, so with nothing picked a third of a laptop screen said "Select a contribution", and
	     the queue (the thing the page is for) got ~670px and overlapped itself. Now the queue is full
	     width until a row is picked; picking one PUSHES the panel in beside it. Push, not a drawer: a
	     drawer covers the queue, and an adviser working down twenty payments loses their place on
	     every one. The queue folds its own columns to fit (`work-queue.svelte`). The X and Esc put it
	     back. Below `xl` there is no room for both, so the panel takes the queue's place until it is
	     closed; the queue stays mounted under it, so its scroll position and filters survive. -->
	<div class="flex min-h-0 flex-1 gap-4 overflow-hidden">
		<!-- The queue card fills its column, pager included, so it and the classify panel beside it are
		     the same height. The pager was a sibling of the card, which made this column shorter than
		     the panel by exactly the pager's height. -->
		<div
			class={cn(
				'flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden',
				selected && 'max-xl:hidden'
			)}
		>
			<!-- WorkQueue owns the search/filter row. It always did; a second toolbar here rendered
			     the controls twice. Server mode is passed THROUGH to it rather than duplicated. -->
			<WorkQueue {rows} bind:selected={selectedUid} {listing} {facets} {onquery} {links}>
				{#snippet pager()}
					{#if pageRange}
						<!-- `PaginationStrip` directly rather than `Pagination`, because the label counts
						     the detected credits in and the page count does not — see `pageRange` above.
						     Nothing at all when there is nothing on screen to count. -->
						<PaginationStrip
							label={pageRange.label}
							page={pageRange.page}
							pageCount={pageRange.pageCount}
							href={pageHref}
						/>
					{/if}
				{/snippet}
			</WorkQueue>
		</div>
		{#if selected}
			<!-- A flex sibling of a fixed width, so the queue gives up exactly this much and keeps every
			     row it had on screen. The panel scrolls inside itself, so a long detail cannot push the
			     queue off-screen. It slides in from the edge it sits on (`panel-motion.ts`); picking
			     another row brings in only its contents (`motion-item-enter`). -->
			<aside
				aria-label="Selected contribution"
				class="flex min-h-0 min-w-0 flex-1 flex-col xl:w-[380px] xl:flex-none 2xl:w-[420px]"
				in:panelSlide
				out:panelSlide
			>
				{#key selected.uid}
					<div class="motion-item-enter flex min-h-0 flex-1 flex-col">
						<ClassifyPanel
							contribution={selected}
							{busy}
							{links}
							onsave={canSave ? save : undefined}
							onlodge={actions?.lodge}
							oncancel={actions?.cancel}
							onrefresh={actions?.refreshFromMorganLloyd}
							onclose={close}
						/>
					</div>
				{/key}
			</aside>
		{/if}
	</div>
</div>
