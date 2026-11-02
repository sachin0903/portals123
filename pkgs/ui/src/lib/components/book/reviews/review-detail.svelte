<script lang="ts">
	// One held command — approve, reject, or cancel. Mutations only exist when the app
	// passes `actions` (SvelteKit form-action names + its own `enhance`).
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent, CardHeader, CardTitle } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { Label } from '../../ui/label/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import ReviewChange from './review-change.svelte';
	import {
		REVIEW_STATUS_LABELS,
		REVIEW_STATUS_TONES,
		reviewCaseRef,
		reviewChangeTitle
	} from './reviews.js';
	import type { ReviewDetailLinks, ReviewRow } from './types.js';

	export type ReviewDetailActions = {
		enhance: Enhance;
		approve?: string;
		reject?: string;
		cancel?: string;
	};

	let {
		review,
		links,
		actions = null
	}: {
		review: ReviewRow;
		links: ReviewDetailLinks;
		actions?: ReviewDetailActions | null;
	} = $props();

	const enhanceForm = $derived(actions?.enhance ?? (() => {}));
	let rejectOpen = $state(false);
	let rejectNote = $state('');
	let rejectForm: HTMLFormElement | null = $state(null);
	let approveOpen = $state(false);
	let approveForm: HTMLFormElement | null = $state(null);
	let cancelOpen = $state(false);
	let cancelForm: HTMLFormElement | null = $state(null);
	let deciding = $state(false);
</script>

<div class="mx-auto flex w-full max-w-2xl flex-col gap-4">
	<p>
		<a href={links.reviews()} class={LINK_RECORD + ' text-sm'}>← Reviews</a>
	</p>

	<Card glass size="sm">
		<CardHeader class="flex flex-row items-start justify-between gap-3">
			<div class="min-w-0">
				<CardTitle class="text-base">{reviewChangeTitle(review)}</CardTitle>
				<p class="text-muted-foreground mt-0.5 text-xs">
					<RecordId label="Case" value={reviewCaseRef(review.case_no)} class="inline" />
					{#if review.proposed_by_name}
						{' · '}submitted by {review.proposed_by_name}
					{/if}
				</p>
			</div>
			<StatusBadge tone={REVIEW_STATUS_TONES[review.status]}>
				{REVIEW_STATUS_LABELS[review.status]}
			</StatusBadge>
		</CardHeader>
		<CardContent class="space-y-4 text-sm">
			<ReviewChange
				{review}
				clientHref={review.contact_uid && links.client ? links.client(review.contact_uid) : null}
				accountHref={review.account_uid && links.account ? links.account(review.account_uid) : null}
			/>

			{#if review.justification}
				<div>
					<p class="text-muted-foreground text-xs">Why</p>
					<p class="mt-0.5 whitespace-pre-wrap">{review.justification}</p>
				</div>
			{/if}

			{#if review.decision_note}
				<div>
					<p class="text-muted-foreground text-xs">Decision</p>
					<p class="mt-0.5 whitespace-pre-wrap">{review.decision_note}</p>
				</div>
			{/if}

			{#if review.status === 'pending' && actions}
				<div class="flex flex-wrap justify-end gap-2 pt-1">
					{#if actions.cancel && review.is_mine}
						<form
							bind:this={cancelForm}
							method="POST"
							action={actions.cancel}
							use:enhanceForm={withToast('Request cancelled', { pending: (v) => (deciding = v) })}
						>
							<Button
								type="button"
								variant="ghost"
								disabled={deciding}
								onclick={() => (cancelOpen = true)}
							>
								Cancel request
							</Button>
						</form>
					{/if}
					{#if actions.reject && review.can_decide}
						<form
							bind:this={rejectForm}
							method="POST"
							action={actions.reject}
							use:enhanceForm={withToast('Change rejected', { pending: (v) => (deciding = v) })}
							class="contents"
						>
							<input type="hidden" name="note" value={rejectNote} />
							<Button
								type="button"
								variant="destructive"
								disabled={deciding}
								onclick={() => (rejectOpen = true)}
							>
								Reject
							</Button>
						</form>
					{/if}
					{#if actions.approve && review.can_decide}
						<form
							bind:this={approveForm}
							method="POST"
							action={actions.approve}
							use:enhanceForm={withToast('Change approved', { pending: (v) => (deciding = v) })}
						>
							<Button type="button" disabled={deciding} onclick={() => (approveOpen = true)}>
								Approve
							</Button>
						</form>
					{/if}
				</div>
			{/if}
		</CardContent>
	</Card>
</div>

<ConfirmDialog
	bind:open={approveOpen}
	title="Approve this change?"
	description="The change will apply now. Automations and notifications will fire as if the person who submitted it had just saved."
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
	title="Reject this change?"
	description="Nothing will apply. Say why — the person who submitted it will see this."
	confirmLabel="Reject"
	destructive
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
