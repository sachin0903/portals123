<script lang="ts">
	// The Reviews queue — the same listing frame as clients, accounts and journeys: one screen,
	// glass card, ListingToolbar, DataTable that scrolls. Expanding a row is the review.
	//
	// The reader takes a SEAT on the queue: what is theirs to decide, what they submitted and are
	// waiting on, everything waiting, or what has been decided. The columns say what a seat needs
	// — how long a case has waited and whose move it is — and the expanded row is the decision:
	// the change with its unchanged rows folded, who · why · who decides, then Reject · Approve,
	// and the next case for the reader opens once this one is decided.
	import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import {
		DataTable,
		type DataTableColumn,
		type DataTableSort
	} from '../../ui/data-table/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Label } from '../../ui/label/index.js';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { TruncatedText } from '../../ui/truncated-text/index.js';
	import { type FilterDef, ListingToolbar } from '../listing/index.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import {
		defaultReviewSeat,
		nextReviewFor,
		REVIEW_SEAT_LABELS,
		REVIEW_SEATS,
		REVIEW_STATUS_LABELS,
		REVIEW_STATUS_TONES,
		reviewApproveCopy,
		reviewCaseRef,
		reviewChangeDetail,
		reviewChangeTitle,
		reviewDecides,
		reviewDecidesSentence,
		reviewRejectCopy,
		reviewSeatCounts,
		reviewSeatOf,
		reviewStatusOptions,
		reviewSubject,
		reviewSubmittedAt,
		reviewSubmitters,
		reviewUndecidable,
		reviewWaiting,
		visibleReviews
	} from './reviews.js';
	import ReviewChange from './review-change.svelte';
	import type { ReviewRow, ReviewSeat, ReviewsListingLinks } from './types.js';

	export type ReviewsListingActions = {
		enhance: Enhance;
		approve?: string;
		reject?: string;
		cancel?: string;
		/** Nudge the approvers of a case the reader submitted (`?/remind`, posting `uid`). */
		remind?: string;
	};

	export type ReviewsListingFilters = {
		submitted_by: string[];
		action_key: string;
		status: string;
		q: string;
		/** The seat, as the URL names it; absent ⇒ the reader's default. */
		seat?: string | null;
	};

	let {
		reviews,
		loadError = false,
		filters = { submitted_by: [], action_key: '', status: '', q: '', seat: null },
		actionOptions = [],
		openUid = null,
		links,
		actions = null,
		onfilters,
		onopen,
		onretry,
		now = Date.now()
	}: {
		reviews: ReviewRow[];
		loadError?: boolean;
		filters?: ReviewsListingFilters;
		actionOptions?: { value: string; label: string }[];
		openUid?: string | null;
		links: ReviewsListingLinks;
		actions?: ReviewsListingActions | null;
		onfilters: (next: ReviewsListingFilters) => void;
		onopen?: (uid: string | null) => void;
		onretry?: () => void;
		/** The clock the waits are read against — passed in so SSR and the browser agree. */
		now?: number;
	} = $props();

	const enhanceForm = $derived(actions?.enhance ?? (() => {}));
	const seat = $derived<ReviewSeat>(reviewSeatOf(filters.seat) ?? defaultReviewSeat(reviews));
	const counts = $derived(reviewSeatCounts(reviews));
	const seatOptions = $derived(
		REVIEW_SEATS.map((value) => ({
			value,
			label:
				counts[value] != null
					? `${REVIEW_SEAT_LABELS[value]} · ${counts[value]}`
					: REVIEW_SEAT_LABELS[value]
		}))
	);
	const settled = $derived(seat === 'settled');

	const filterDefs = $derived<FilterDef[]>([
		{
			key: 'submitted_by',
			label: 'Submitted by',
			options: reviewSubmitters(reviews)
		},
		{
			key: 'action_key',
			label: 'Action',
			options: actionOptions,
			single: true,
			allLabel: 'All actions'
		},
		// The outcome only means something under Settled; the other seats are all Waiting.
		...(settled
			? [
					{
						key: 'status',
						label: 'Outcome',
						options: reviewStatusOptions().filter((option) => option.value !== 'all'),
						single: true,
						allLabel: 'Any outcome'
					} satisfies FilterDef
				]
			: [])
	]);

	const barValue = $derived({
		...(filters.submitted_by.length ? { submitted_by: filters.submitted_by } : {}),
		...(filters.action_key ? { action_key: [filters.action_key] } : {}),
		...(settled && filters.status ? { status: [filters.status] } : {})
	});

	const rows = $derived(visibleReviews(reviews, { ...filters, seat }, openUid));
	const dirty = $derived(
		filters.submitted_by.length > 0 ||
			Boolean(filters.action_key) ||
			(settled && Boolean(filters.status)) ||
			filters.q.trim() !== ''
	);
	const late = $derived(
		rows.filter((row) => row.status === 'pending' && reviewWaiting(row, now).tone !== 'none').length
	);
	const othersWaiting = $derived(
		seat === 'for_me' ? Math.max(0, (counts.waiting ?? 0) - (counts.for_me ?? 0)) : 0
	);

	const caseColumn: DataTableColumn<ReviewRow> = {
		key: 'case',
		header: 'Case',
		sortable: true,
		sortDefault: true,
		sortValue: (row) => row.case_no,
		headerClass: 'w-[6rem]'
	};
	const changeColumn: DataTableColumn<ReviewRow> = {
		key: 'change',
		header: 'Change',
		sortable: true,
		sortValue: (row) => reviewChangeTitle(row),
		headerClass: 'w-[34%]'
	};
	const clientColumn: DataTableColumn<ReviewRow> = {
		key: 'client',
		header: 'Client',
		sortable: true,
		sortValue: (row) => reviewSubject(row),
		headerClass: 'w-[16%]'
	};
	const submittedColumn: DataTableColumn<ReviewRow> = {
		key: 'submitted_by',
		header: 'Submitted by',
		sortable: true,
		sortValue: (row) => row.proposed_by_name,
		hideBelowWidth: 'md',
		headerClass: 'w-[14%]'
	};
	const pendingColumns: DataTableColumn<ReviewRow>[] = [
		caseColumn,
		changeColumn,
		clientColumn,
		submittedColumn,
		{
			key: 'waiting',
			header: 'Waiting',
			sortable: true,
			sortValue: (row) => row.proposed_at,
			headerClass: 'w-[6rem]'
		},
		{
			key: 'decides',
			header: 'Decides',
			sortable: true,
			sortValue: (row) => (row.can_decide ? '' : reviewDecides(row)),
			hideBelowWidth: 'lg',
			headerClass: 'w-[14%]'
		}
	];
	const settledColumns: DataTableColumn<ReviewRow>[] = [
		caseColumn,
		changeColumn,
		clientColumn,
		submittedColumn,
		{
			key: 'decided',
			header: 'Decided',
			sortable: true,
			sortValue: (row) => row.decided_at,
			hideBelowWidth: 'lg',
			headerClass: 'w-[16%]'
		},
		{
			key: 'status',
			header: 'Outcome',
			sortable: true,
			sortValue: (row) => row.status,
			headerClass: 'w-[8rem]'
		}
	];
	// "Submitted by me" is the reader's own cases whatever their state, so it carries both how
	// long each has waited (or took) and its outcome.
	const mineColumns: DataTableColumn<ReviewRow>[] = [
		caseColumn,
		changeColumn,
		clientColumn,
		pendingColumns[4] as DataTableColumn<ReviewRow>,
		{
			key: 'decides',
			header: 'Decides',
			sortable: true,
			sortValue: (row) => reviewDecides(row),
			hideBelowWidth: 'lg',
			headerClass: 'w-[14%]'
		},
		settledColumns[5] as DataTableColumn<ReviewRow>
	];
	const columns = $derived(
		settled ? settledColumns : seat === 'mine' ? mineColumns : pendingColumns
	);

	let sort = $state<DataTableSort | null>(null);
	let deciding = $state(false);
	let rejectOpen = $state(false);
	let rejectNote = $state('');
	let rejectForm: HTMLFormElement | null = $state(null);
	let approveOpen = $state(false);
	let approveForm: HTMLFormElement | null = $state(null);
	let cancelOpen = $state(false);
	let cancelForm: HTMLFormElement | null = $state(null);

	const opened = $derived(rows.find((row) => row.uid === openUid) ?? null);
	const approveCopy = $derived(opened ? reviewApproveCopy(opened) : null);
	const rejectCopy = $derived(opened ? reviewRejectCopy(opened) : null);

	function toggle(row: ReviewRow) {
		const next = openUid === row.uid ? null : row.uid;
		onopen?.(next);
	}

	function setSeat(next: string) {
		// A seat change clears the outcome filter: it belongs to Settled alone.
		onfilters({ ...filters, status: '', seat: next });
	}

	function setBar(next: Record<string, string[]>) {
		onfilters({
			...filters,
			seat,
			submitted_by: next.submitted_by ?? [],
			action_key: next.action_key?.[0] ?? '',
			status: settled ? (next.status?.[0] ?? '') : ''
		});
	}

	function clear() {
		onfilters({ submitted_by: [], action_key: '', status: '', q: '', seat });
	}

	/** After a decision, the next case for the reader opens; none left closes the row. */
	function decided(uid: string) {
		rejectNote = '';
		onopen?.(nextReviewFor(rows, uid));
	}

	/** The tint on a row that has waited: amber past a day, red past three, as on Runs. */
	function rowClass(row: ReviewRow): string | undefined {
		if (row.status !== 'pending') return undefined;
		const tone = reviewWaiting(row, now).tone;
		if (tone === 'danger') return 'shadow-[inset_3px_0_0_var(--color-destructive)]';
		if (tone === 'warning') return 'shadow-[inset_3px_0_0_var(--color-warning)]';
		return undefined;
	}

	/** J / K move through the queue, A and R open the decision for the open case — unless a
	 *  field, a menu or a dialog has the keyboard. */
	function onkeydown(event: KeyboardEvent): void {
		if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
		const target = event.target as HTMLElement | null;
		if (target?.closest('input, textarea, select, [contenteditable="true"]')) return;
		if (
			document.querySelector(
				'[role="dialog"][data-state="open"], [role="menu"][data-state="open"], [role="listbox"]'
			)
		)
			return;
		const key = event.key.toLowerCase();
		if (key === 'j' || key === 'k') {
			if (!rows.length) return;
			const at = rows.findIndex((row) => row.uid === openUid);
			const next =
				key === 'j'
					? rows[Math.min(rows.length - 1, at + 1)]
					: rows[Math.max(0, at < 0 ? 0 : at - 1)];
			if (next && next.uid !== openUid) onopen?.(next.uid);
			event.preventDefault();
		} else if (
			key === 'a' &&
			opened?.can_decide &&
			actions?.approve &&
			opened.status === 'pending'
		) {
			approveOpen = true;
			event.preventDefault();
		} else if (
			key === 'r' &&
			opened?.can_decide &&
			actions?.reject &&
			opened.status === 'pending'
		) {
			rejectOpen = true;
			event.preventDefault();
		}
	}
</script>

<svelte:window {onkeydown} />

{#snippet emptyReviews()}
	<EmptyState
		icon={ClipboardCheckIcon}
		title={dirty
			? 'No reviews match your filters'
			: seat === 'for_me'
				? 'Nothing for you to decide'
				: seat === 'mine'
					? 'Nothing of yours is waiting'
					: seat === 'settled'
						? 'Nothing has been decided yet'
						: 'Nothing waiting'}
		description={dirty
			? 'Clear the filters to see the rest of the queue.'
			: seat === 'for_me' && othersWaiting
				? `${othersWaiting} ${othersWaiting === 1 ? 'case is' : 'cases are'} waiting on other people.`
				: 'When someone submits a change that needs a second pair of eyes, it lands here.'}
	/>
{/snippet}

{#snippet seatSwitch()}
	<Segmented
		options={seatOptions}
		value={seat}
		size="sm"
		shape="square"
		label="Which reviews"
		onValueChange={setSeat}
	/>
{/snippet}

<!-- ONE SCREEN from `md`. The page is a flex column the height of the viewport under the top bar,
     and the LIST is the only thing that scrolls — so the toolbar stays put however long the queue
     is. On a phone the PAGE scrolls instead. The topbar crumb already names the page. -->
<div
	class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	{#if loadError}
		<LoadErrorBanner message="Couldn’t load reviews. Try again." onRetry={onretry} />
	{/if}

	<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
		<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
			<ListingToolbar
				class="shrink-0"
				query={filters.q}
				placeholder="Search case, client, account or submitter"
				{dirty}
				onclear={clear}
				onsearch={(q) => onfilters({ ...filters, seat, q })}
				{filterDefs}
				filterValue={barValue}
				onfilters={setBar}
				actions={seatSwitch}
			/>

			<DataTable
				{columns}
				{rows}
				bind:sort
				scroll
				fixed
				getKey={(row) => row.uid}
				label="Reviews"
				empty={emptyReviews}
				onRowClick={toggle}
				isExpanded={(row) => openUid === row.uid}
				{rowClass}
			>
				{#snippet cell(row, column)}
					{#if column.key === 'case'}
						<span class="font-mono text-xs tabular-nums">{reviewCaseRef(row.case_no)}</span>
					{:else if column.key === 'change'}
						{@const detail = reviewChangeDetail(row)}
						<TruncatedText text={reviewChangeTitle(row)} class="font-medium" />
						{#if detail}
							<TruncatedText text={detail} class="text-muted-foreground mt-0.5 block text-xs" />
						{/if}
					{:else if column.key === 'client'}
						<TruncatedText
							text={reviewSubject(row)}
							href={row.contact_uid && links.client ? links.client(row.contact_uid) : undefined}
						/>
					{:else if column.key === 'submitted_by'}
						<TruncatedText text={row.proposed_by_name ?? '—'} />
					{:else if column.key === 'waiting'}
						{@const waited = reviewWaiting(row, now)}
						<span
							class="tabular-nums {waited.tone === 'danger'
								? 'text-destructive-strong font-medium'
								: waited.tone === 'warning'
									? 'text-warning-strong font-medium'
									: 'text-muted-foreground'}"
							title={reviewSubmittedAt(row.proposed_at)}>{waited.label}</span
						>
					{:else if column.key === 'decides'}
						{#if row.status !== 'pending'}
							<TruncatedText
								text={row.decided_by_name ?? reviewDecides(row)}
								class="text-muted-foreground"
							/>
						{:else if row.can_decide}
							<StatusBadge tone="primary">You</StatusBadge>
						{:else if reviewUndecidable(row)}
							<StatusBadge tone="danger">Nobody can</StatusBadge>
						{:else}
							<TruncatedText text={reviewDecides(row)} class="text-muted-foreground" />
						{/if}
					{:else if column.key === 'decided'}
						{#if row.decided_at}
							<span class="block truncate">{row.decided_by_name ?? '—'}</span>
							<span class="text-muted-foreground block text-xs"
								>{reviewSubmittedAt(row.decided_at)}</span
							>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'status'}
						<StatusBadge tone={REVIEW_STATUS_TONES[row.status]}>
							{REVIEW_STATUS_LABELS[row.status]}
						</StatusBadge>
					{/if}
				{/snippet}

				{#snippet detail(row)}
					{@const waited = reviewWaiting(row, now)}
					{@const stuck = reviewUndecidable(row)}
					<!-- THE DECISION. The row above names the client, so the body does not; the change
					     comes first with its unchanged rows folded, and the rail says who · why · who
					     decides, then the decision. -->
					<div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_17rem]">
						<div class="min-w-0">
							<ReviewChange review={row} showClient={false} showAccount={false} />
							{#if row.account_uid || row.contact_uid}
								<p class="text-muted-foreground mt-3 flex flex-wrap gap-x-3 text-xs">
									{#if row.contact_uid && links.client}
										<a href={links.client(row.contact_uid)} class="hover:underline"
											>Open the client record ↗</a
										>
									{/if}
									{#if row.account_uid && links.account}
										<a href={links.account(row.account_uid)} class="hover:underline"
											>Open the account ↗</a
										>
									{/if}
								</p>
							{/if}
						</div>
						<div class="space-y-3 text-sm">
							<div>
								<p class="text-muted-foreground text-xs">Submitted</p>
								<p class="mt-0.5">{row.proposed_by_name ?? '—'}</p>
								<p class="text-muted-foreground text-xs">
									{reviewSubmittedAt(row.proposed_at)}{#if !waited.settled}
										· {waited.label} ago{/if}
								</p>
							</div>
							<div>
								<p class="text-muted-foreground text-xs">Why</p>
								{#if row.justification}
									<p class="mt-0.5 whitespace-pre-wrap">{row.justification}</p>
								{:else}
									<p class="text-muted-foreground mt-0.5 text-xs">No reason was asked for.</p>
								{/if}
							</div>
							<div>
								<p class="text-muted-foreground text-xs">Decides</p>
								<p class="mt-0.5">{reviewDecides(row)}</p>
								{#if row.status === 'pending'}
									<p
										class="text-xs {row.can_decide
											? 'text-primary'
											: stuck
												? 'text-destructive-strong'
												: 'text-muted-foreground'}"
									>
										{reviewDecidesSentence(row)}
									</p>
								{/if}
							</div>
							{#if row.decided_at}
								<!-- The decision, not the outcome: a conflict after an approval is the system's,
								     and the badge beside the row already says Conflicted. -->
								{@const decidedBy =
									row.status === 'rejected'
										? 'Rejected by'
										: row.status === 'cancelled'
											? 'Cancelled by'
											: 'Approved by'}
								<div>
									<p class="text-muted-foreground text-xs">{decidedBy}</p>
									<p class="mt-0.5">{row.decided_by_name ?? '—'}</p>
									<p class="text-muted-foreground text-xs">
										{reviewSubmittedAt(row.decided_at)} · after {waited.label}
									</p>
									{#if row.decision_note}
										<p class="mt-1 whitespace-pre-wrap">{row.decision_note}</p>
									{/if}
								</div>
							{/if}
							{#if row.status === 'conflicted'}
								<p class="text-warning-strong text-xs">
									The record changed after this was submitted, so the approval was refused and
									nothing applied. Submit it again from the record.
								</p>
							{/if}
							{#if row.status === 'pending' && actions}
								<div class="border-border/60 flex flex-wrap items-center gap-2 border-t pt-3">
									{#if actions.cancel && row.is_mine && row.source !== 'action'}
										<form
											bind:this={cancelForm}
											method="POST"
											action={actions.cancel}
											use:enhanceForm={withToast('Request cancelled', {
												pending: (v) => (deciding = v)
											})}
										>
											<input type="hidden" name="uid" value={row.uid} />
											<input type="hidden" name="source" value={row.source ?? 'review'} />
											<Button
												type="button"
												variant="ghost"
												size="sm"
												disabled={deciding}
												onclick={() => (cancelOpen = true)}
											>
												Cancel request
											</Button>
										</form>
									{/if}
									{#if actions.remind && row.is_mine && !row.can_decide}
										<form
											method="POST"
											action={actions.remind}
											use:enhanceForm={withToast('Approvers reminded', {
												pending: (v) => (deciding = v)
											})}
										>
											<input type="hidden" name="uid" value={row.uid} />
											<Button type="submit" variant="outline" size="sm" disabled={deciding}>
												Remind approvers
											</Button>
										</form>
									{/if}
									{#if actions.reject && row.can_decide}
										<form
											bind:this={rejectForm}
											method="POST"
											action={actions.reject}
											use:enhanceForm={withToast('Change rejected', {
												pending: (v) => (deciding = v),
												onSuccess: () => decided(row.uid)
											})}
											class="contents"
										>
											<input type="hidden" name="uid" value={row.uid} />
											<input type="hidden" name="source" value={row.source ?? 'review'} />
											<input type="hidden" name="note" value={rejectNote} />
											<Button
												type="button"
												variant="destructive"
												size="sm"
												disabled={deciding}
												onclick={() => (rejectOpen = true)}
											>
												Reject
											</Button>
										</form>
									{/if}
									{#if actions.approve && row.can_decide}
										<form
											bind:this={approveForm}
											method="POST"
											action={actions.approve}
											use:enhanceForm={withToast('Change approved', {
												pending: (v) => (deciding = v),
												onSuccess: () => decided(row.uid)
											})}
											class="ml-auto"
										>
											<input type="hidden" name="uid" value={row.uid} />
											<input type="hidden" name="source" value={row.source ?? 'review'} />
											<Button
												type="button"
												size="sm"
												disabled={deciding}
												onclick={() => (approveOpen = true)}
											>
												Approve
											</Button>
										</form>
									{/if}
								</div>
								{#if stuck}
									<p class="text-destructive-strong text-xs">
										Nobody else holds {reviewDecides(row)}, so this cannot be decided as things
										stand. Ask an administrator to give someone that role.
									</p>
								{:else if row.can_decide}
									<p class="text-muted-foreground text-xs">
										<kbd class="rounded border px-1 font-mono text-[10px]">A</kbd> approve ·
										<kbd class="rounded border px-1 font-mono text-[10px]">R</kbd> reject ·
										<kbd class="rounded border px-1 font-mono text-[10px]">J</kbd>
										<kbd class="rounded border px-1 font-mono text-[10px]">K</kbd> next and previous
									</p>
								{/if}
							{/if}
						</div>
					</div>
				{/snippet}
			</DataTable>

			{#if rows.length}
				<p
					class="text-muted-foreground flex shrink-0 flex-wrap justify-between gap-x-4 gap-y-1 text-xs tabular-nums"
				>
					<span>
						{#if seat === 'for_me'}
							{rows.length} for you to decide · oldest first{#if late}
								· {late} waiting more than a day{/if}
						{:else if seat === 'mine'}
							{rows.length} submitted by you · {rows.filter((row) => row.status === 'pending')
								.length} waiting{#if late}
								· {late} for more than a day{/if}
						{:else if seat === 'waiting'}
							{rows.length} waiting · oldest first{#if late}
								· {late} for more than a day{/if}
						{:else}
							{rows.length} settled
						{/if}
					</span>
					{#if seat === 'for_me' && othersWaiting}
						<button type="button" class="hover:underline" onclick={() => setSeat('waiting')}>
							See the {othersWaiting} waiting on others
						</button>
					{/if}
				</p>
			{/if}
		</CardContent>
	</Card>
</div>

<ConfirmDialog
	bind:open={approveOpen}
	title={approveCopy?.title ?? 'Approve this change?'}
	description={approveCopy?.description ??
		'The change will apply now. Automations and notifications will fire as if the person who submitted it had just saved.'}
	confirmLabel="Approve"
	onconfirm={() => approveForm?.requestSubmit()}
/>

<ConfirmDialog
	bind:open={cancelOpen}
	title="Cancel this request?"
	description="Nothing will change. You can submit again from the record."
	confirmLabel="Cancel request"
	onconfirm={() => cancelForm?.requestSubmit()}
/>

<ConfirmDialog
	bind:open={rejectOpen}
	title={rejectCopy?.title ?? 'Reject this change?'}
	description={rejectCopy?.description ??
		'Nothing will apply. Say why — the person who submitted it will see this.'}
	confirmLabel="Reject"
	destructive
	confirmDisabled={!rejectNote.trim()}
	onconfirm={() => {
		if (!rejectNote.trim()) return;
		rejectForm?.requestSubmit();
	}}
>
	<div class="space-y-1">
		<Label for="reject_note">Reason</Label>
		<Textarea id="reject_note" bind:value={rejectNote} rows={3} required />
	</div>
</ConfirmDialog>
