<script lang="ts">
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import FileSignatureIcon from '@lucide/svelte/icons/file-signature';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import PartyPopperIcon from '@lucide/svelte/icons/party-popper';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import UploadIcon from '@lucide/svelte/icons/upload';
	import { Button } from '../ui/button/index.js';
	import type { OnboardingNextStep } from './types.js';

	// The one thing a client should see first on /onboarding: what to do next, or — when nothing is
	// theirs to do — what is actually happening and who holds it. It answers a question the page
	// could not answer before: the sections strip and a "3/8 steps done" line both describe the
	// application without ever naming an action, so a client landing here had to go looking for
	// their own work. Three mutually exclusive states, in the order they matter:
	//
	//   1. complete   — every step settled; a plain thank-you, no controls.
	//   2. next       — a step is available NOW: name it and offer the one button that starts it.
	//   3. waiting    — a live journey holding on somebody else (usually the adviser's fact-find), or
	//                   on what the caller names (`waitingTitle`: the client's identity photos being
	//                   checked).
	//
	// The waiting state is the reason this exists at all. In the seeded flow a client's own work is
	// the terms declarations and the identity check, and both sit BEHIND staff steps — so for much
	// of a real application there is nothing for them to press, and a page that only lists tasks
	// reads as broken precisely when it is working correctly. Naming the current milestone is the
	// difference between "stuck" and "in progress".
	//
	// Presentation only: the caller owns what "start" does (each step type opens its own dialog),
	// which keeps the fulfilment plumbing in the app and this component mountable in the studio.
	let {
		next = null,
		done,
		total,
		stageLabel = null,
		journeyComplete = null,
		waitingWith = 'adviser',
		waitingTitle = null,
		waitingDetail = null,
		onstart
	}: {
		/** The step the client can act on now, with its position among the steps they still owe. */
		next?: OnboardingNextStep | null;
		done: number;
		total: number;
		/** The current whole-journey milestone, shown when the client has nothing to do. */
		stageLabel?: string | null;
		/** Whether the JOURNEY is finished, which is not the same question as whether the client's
		 *  current steps are. Their list GROWS as the journey advances — identity is released after
		 *  the terms are signed — so `done >= total` alone would announce "all set" in the gap and
		 *  then produce another task. Null falls back to the counts, for a caller that has no
		 *  milestone rollup to consult. */
		journeyComplete?: boolean | null;
		/** Who a waiting application is held on. Only ever staff-side — a client is never told an
		 *  identity or AML review is with Graphene's own compliance team. */
		waitingWith?: 'adviser' | 'us';
		/** What the application is waiting on, when the caller knows better than "your adviser":
		 *  e.g. the client's own identity photos being checked. Replaces the waiting heading. */
		waitingTitle?: string | null;
		/** The line under `waitingTitle`. Replaces the milestone line. */
		waitingDetail?: string | null;
		onstart?: () => void;
	} = $props();

	const complete = $derived(journeyComplete ?? (total > 0 && done >= total));
	const pct = $derived(total > 0 ? Math.max(0, Math.min(100, (done / total) * 100)) : 0);

	const ICON = {
		fill_form: FileTextIcon,
		accept_terms: FileSignatureIcon,
		upload_document: UploadIcon,
		verify_identity: ScanFaceIcon
	} as const;
	// Capitalised so it can be rendered as `<Icon />` directly: a `{@const}` cannot live inside a
	// plain element, and `<svelte:component>` is gone in Svelte 5.
	const Icon = $derived(
		complete
			? PartyPopperIcon
			: next
				? ((ICON as Record<string, typeof FileTextIcon>)[next.step_type] ?? ClipboardListIcon)
				: ClockIcon
	);

	// The button says what the step IS, not "continue" — a client about to photograph their passport
	// should know that before they press anything.
	const CTA: Record<string, string> = {
		accept_terms: 'Review and agree',
		upload_document: 'Upload document',
		verify_identity: 'Verify my identity',
		fill_form: 'Fill in details'
	};
	const cta = $derived(next ? (CTA[next.step_type] ?? 'Continue') : null);

	const KIND: Record<string, string> = {
		accept_terms: 'Agreement',
		upload_document: 'Document upload',
		verify_identity: 'Identity check',
		fill_form: 'Form'
	};
	const kind = $derived(next ? (KIND[next.step_type] ?? 'Step') : null);
</script>

<section
	class="border-brand/25 from-brand/[0.07] rounded-xl border bg-gradient-to-br via-transparent to-transparent p-5 sm:p-6"
	aria-label="What happens next"
>
	<div class="flex items-start gap-4">
		<div
			class="flex size-11 shrink-0 items-center justify-center rounded-xl {complete
				? 'bg-success/10 text-success'
				: next
					? 'bg-brand/10 text-brand'
					: 'bg-muted text-muted-foreground'}"
		>
			<Icon class="size-5" aria-hidden="true" />
		</div>

		<div class="min-w-0 flex-1 space-y-1">
			{#if complete}
				<h2 class="text-xl font-semibold tracking-tight">You’re all set. Thank you.</h2>
				<p class="text-muted-foreground text-sm">
					You’ve finished everything we needed from you. There’s nothing left for you to do.
				</p>
			{:else if next}
				<p class="text-brand text-xs font-semibold tracking-wide uppercase">
					{#if next.total > 1}Next of {next.total}{:else}Next{/if}
				</p>
				<h2 class="text-xl font-semibold tracking-tight break-words">{next.title}</h2>
				<p class="text-muted-foreground text-sm">
					{kind}{#if next.total > 1}
						· {next.total - next.index} still to do after this{/if}
				</p>
			{:else if waitingTitle}
				<h2 class="text-xl font-semibold tracking-tight break-words">{waitingTitle}</h2>
				{#if waitingDetail}
					<p class="text-muted-foreground text-sm">{waitingDetail}</p>
				{/if}
			{:else}
				<h2 class="text-xl font-semibold tracking-tight">
					{#if waitingWith === 'us'}We’re working on your application{:else}Your adviser is working
						on your application{/if}
				</h2>
				<p class="text-muted-foreground text-sm">
					{#if stageLabel}
						Currently at <span class="text-foreground font-medium">{stageLabel}</span>. We’ll let
						you know as soon as something needs you.
					{:else}
						Nothing needs you right now. We’ll let you know as soon as something does.
					{/if}
				</p>
			{/if}
		</div>

		{#if next && cta}
			<Button class="mt-0.5 hidden shrink-0 sm:inline-flex" onclick={onstart}>{cta}</Button>
		{/if}
	</div>

	{#if next && cta}
		<!-- On a phone the action belongs under the copy at full width, not squeezed beside it. -->
		<Button class="mt-4 w-full sm:hidden" onclick={onstart}>{cta}</Button>
	{/if}

	{#if total > 0}
		<div class="mt-5 space-y-1.5">
			<div class="flex items-baseline justify-between gap-2 text-xs">
				<span class="text-muted-foreground">Your progress</span>
				<span class="font-medium tabular-nums">{done} of {total} done</span>
			</div>
			<div
				class="bg-brand/15 h-1.5 overflow-hidden rounded-full"
				role="progressbar"
				aria-valuenow={done}
				aria-valuemin={0}
				aria-valuemax={total}
				aria-label="Onboarding progress"
			>
				<div class="bg-brand h-full rounded-full transition-[width]" style:width="{pct}%"></div>
			</div>
		</div>
	{/if}
</section>
