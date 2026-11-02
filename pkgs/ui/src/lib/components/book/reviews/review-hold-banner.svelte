<script lang="ts">
	// A held change, said ON the card it will change: one amber line with the case, who submitted
	// it and when, what it touches, who decides it, and the way to the case. The record used to
	// put the whole proposal in a block above every card, so the tax card read as unchanged while
	// its change sat four cards up; the vulnerability card only changed its look once the change
	// had APPLIED. This is the "modified, waiting" state, on the thing that is modified.
	import ClockIcon from '@lucide/svelte/icons/clock';
	import { Button } from '../../ui/button/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import {
		reviewCaseRef,
		reviewChangedFields,
		reviewDecidesSentence,
		reviewUndecidable,
		reviewWaiting
	} from './reviews.js';
	import type { ReviewRow } from './types.js';

	let {
		review,
		href = null,
		what = null,
		now = Date.now(),
		cancelAction = null,
		enhance: enhanceForm = null,
		oncancel = null,
		class: className = ''
	}: {
		review: ReviewRow;
		/** The case on the Reviews queue. */
		href?: string | null;
		/** What the hold touches, when the caller knows better than the field list ("Vulnerability"). */
		what?: string | null;
		now?: number;
		/** The form action that withdraws the case (`?/cancelReview`, posting `review_uid`), offered
		 *  to the person who submitted it. */
		cancelAction?: string | null;
		enhance?: Enhance | null;
		/** A callback instead of a form, for a mount that cancels through its own fetch. */
		oncancel?: ((uid: string) => void | Promise<unknown>) | null;
		class?: string;
	} = $props();

	const fields = $derived(reviewChangedFields(review));
	const touches = $derived(what ?? (fields.length ? fields.join(', ') : null));
	const waited = $derived(reviewWaiting(review, now));
	const stuck = $derived(reviewUndecidable(review));
	const canCancel = $derived(
		review.is_mine && ((Boolean(cancelAction) && Boolean(enhanceForm)) || Boolean(oncancel))
	);

	let cancelOpen = $state(false);
	let cancelForm: HTMLFormElement | null = $state(null);
	let cancelling = $state(false);
</script>

<div
	class="border-warning-line bg-warning-subtle flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border px-3 py-2 text-xs {className}"
	role="status"
>
	<span class="text-warning-strong inline-flex items-center gap-1.5 font-semibold">
		<ClockIcon class="size-3.5 shrink-0" aria-hidden="true" />
		Waiting for approval
	</span>
	<span class="font-mono text-[11px] font-semibold">{reviewCaseRef(review.case_no)}</span>
	<span class="text-muted-foreground min-w-0">
		{#if touches}<span class="text-foreground">{touches}</span> ·{/if}
		{review.proposed_by_name ?? 'Someone'} · {waited.label} ago ·
		<span class={stuck ? 'text-destructive-strong font-medium' : ''}
			>{reviewDecidesSentence(review)}</span
		>
	</span>
	{#if review.justification}
		<span class="text-muted-foreground basis-full truncate">“{review.justification}”</span>
	{/if}
	<span class="ml-auto flex items-center gap-1">
		{#if canCancel && cancelAction && enhanceForm}
			<form
				bind:this={cancelForm}
				method="POST"
				action={cancelAction}
				use:enhanceForm={withToast('Request cancelled', { pending: (v) => (cancelling = v) })}
			>
				<input type="hidden" name="review_uid" value={review.uid} />
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={cancelling}
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
				disabled={cancelling}
				onclick={() => (cancelOpen = true)}
			>
				Cancel request
			</Button>
		{/if}
		{#if href}
			<a {href} class="{LINK_RECORD} text-xs font-medium">Open case</a>
		{/if}
	</span>
</div>

{#if canCancel}
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
			cancelling = true;
			void Promise.resolve(oncancel(review.uid)).finally(() => (cancelling = false));
		}}
	/>
{/if}
