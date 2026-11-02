<script lang="ts">
	// The pending change on a record — case, who, why, then the action-specific body.
	import { Button } from '../../ui/button/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import ReviewChange from './review-change.svelte';
	import {
		REVIEW_STATUS_LABELS,
		REVIEW_STATUS_TONES,
		reviewCaseRef,
		reviewSubmittedAt
	} from './reviews.js';
	import type { ReviewRow } from './types.js';

	let {
		review,
		href = null,
		clientHref = null,
		accountHref = null,
		showClient = false,
		showAccount = true,
		cancelAction = null,
		enhance: enhanceForm = null,
		oncancel = null
	}: {
		review: ReviewRow;
		href?: string | null;
		clientHref?: string | null;
		accountHref?: string | null;
		/** The record already names the client. The queue still shows them. */
		showClient?: boolean;
		/** Keep true on the client record so an account-targeted hold names the account. */
		showAccount?: boolean;
		cancelAction?: string | null;
		enhance?: Enhance | null;
		oncancel?: ((uid: string) => void | Promise<unknown>) | null;
	} = $props();

	let cancelOpen = $state(false);
	let cancelForm: HTMLFormElement | null = $state(null);
	let deciding = $state(false);

	const canCancel = $derived(review.is_mine && (Boolean(cancelAction) || Boolean(oncancel)));
	const caseRef = $derived(reviewCaseRef(review.case_no));
</script>

<div class="space-y-3">
	<div class="flex flex-wrap items-start justify-between gap-3">
		<div class="min-w-0 space-y-1">
			<div class="flex flex-wrap items-center gap-2">
				<StatusBadge tone={REVIEW_STATUS_TONES[review.status]}>
					{REVIEW_STATUS_LABELS[review.status]}
				</StatusBadge>
				{#if href}
					<a {href} class="text-xs">
						<RecordId label="Case" value={caseRef} />
					</a>
				{:else}
					<RecordId label="Case" value={caseRef} />
				{/if}
			</div>
			<p class="text-sm">
				{review.proposed_by_name ?? 'Someone'}
				<span class="text-muted-foreground">
					· {reviewSubmittedAt(review.proposed_at)}
				</span>
			</p>
			{#if review.justification}
				<p class="text-muted-foreground whitespace-pre-wrap text-sm">{review.justification}</p>
			{/if}
		</div>
		<div class="flex flex-wrap justify-end gap-1">
			{#if href}
				<Button variant="ghost" size="sm" {href}>Open review</Button>
			{/if}
			{#if canCancel && cancelAction && enhanceForm}
				<form
					bind:this={cancelForm}
					method="POST"
					action={cancelAction}
					use:enhanceForm={withToast('Request cancelled', { pending: (v) => (deciding = v) })}
				>
					<input type="hidden" name="review_uid" value={review.uid} />
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
			{:else if canCancel && oncancel}
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={deciding}
					onclick={() => (cancelOpen = true)}
				>
					Cancel request
				</Button>
			{/if}
		</div>
	</div>

	<ReviewChange {review} {clientHref} {accountHref} {showClient} {showAccount} />
</div>

<ConfirmDialog
	bind:open={cancelOpen}
	title="Cancel this request?"
	description="Nothing will change. You can submit again from this record."
	confirmLabel="Cancel request"
	onconfirm={() => {
		if (cancelForm) {
			cancelForm.requestSubmit();
			return;
		}
		if (!oncancel) return;
		deciding = true;
		void Promise.resolve(oncancel(review.uid)).finally(() => (deciding = false));
	}}
/>
