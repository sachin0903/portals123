<script lang="ts">
	// One run, beside the runs queue — the transfers detail panel's shape for a run: identity and
	// state, the provider's reason, the saga rail of its children with who has each one, who it is
	// waiting on, the last few events, and the one next action in the footer. The full page is a
	// link away; this is enough to decide, retry or move on without leaving the queue.
	//
	// For a HELD run the panel is the decision: the same change body the Reviews queue draws (a
	// bank change shows its digits, a payment its figure), who may decide it and how many could,
	// when the approvers were last reminded, and Approve · Reject · Remind · Cancel in the footer.
	import BellIcon from '@lucide/svelte/icons/bell';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import XIcon from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { Button } from '../ui/button/index.js';
	import { Card, CardContent } from '../ui/card/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { RecordId } from '../ui/record-id/index.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { LINK_RECORD } from '../../text-shell.js';
	import type { Enhance } from '../book/clients/form-enhance.js';
	import ReviewChange from '../book/reviews/review-change.svelte';
	import {
		reviewApproveCopy,
		reviewInputFacts,
		reviewRejectCopy,
		waitingLabel,
		waitingTone
	} from '../book/reviews/reviews.js';
	import DecideDialogs from './decide-dialogs.svelte';
	import { nextAction } from './next-action.js';
	import { reviewRowFromRun } from './review-row.js';
	import type { RunDetailActions } from './run-detail.svelte';
	import type { RunDetailData, RunLinks } from './run-types.js';
	import {
		clockLabel,
		eventLabel,
		formatDuration,
		nodeDurationMs,
		pathOf,
		providerLabel,
		relativeTime,
		runDurationMs,
		runStatusLabel,
		runStatusTone,
		stepToneClass
	} from './run-states.js';

	let {
		run,
		canManage = false,
		enhance,
		links,
		actions = {},
		onclose,
		onrefresh,
		now = Date.now()
	}: {
		run: RunDetailData;
		canManage?: boolean;
		enhance?: Enhance;
		links: RunLinks;
		actions?: RunDetailActions;
		/** Closes the panel. Absent ⇒ no X. */
		onclose?: () => void;
		onrefresh?: () => void;
		now?: number;
	} = $props();

	const next = $derived(nextAction(run));
	const canAct = $derived(canManage && !!enhance);
	const showRetry = $derived(canAct && next.kind === 'retry' && !!actions.retry);
	const showDecide = $derived(canAct && next.kind === 'decide' && !!actions.decide);
	const showCancel = $derived(canAct && next.canCancel && !!actions.cancel);
	const held = $derived(run.status === 'awaiting_approval');
	const showRemind = $derived(held && canAct && !!actions.remind);
	const rail = $derived(run.children.length ? run.children : [run]);
	// The last few events, naming the node when it is not the run itself, and folding a burst of
	// the same word at the same second (a refused leaf, its parent and the root all go "needs
	// attention" together) into one line.
	const recent = $derived.by(() => {
		const out: typeof run.events = [];
		for (const event of run.events) {
			const prev = out.at(-1);
			if (prev && prev.type === event.type && prev.at === event.at) continue;
			out.push(event);
		}
		return out.slice(-4);
	});

	let cancelOpen = $state(false);
	let cancelForm = $state<HTMLFormElement | null>(null);

	function after(verb: string) {
		return () =>
			async ({
				result,
				update
			}: {
				result: { type: string; data?: { error?: string } };
				update: (opts?: { reset?: boolean }) => Promise<void>;
			}) => {
				if (result.type === 'success') {
					toast.success(verb);
					onrefresh?.();
				} else {
					toast.error(
						String((result.type === 'failure' && result.data?.error) || 'That did not go through.')
					);
				}
				await update({ reset: false });
			};
	}

	/** Who the run is waiting on, in one line. */
	const waitingOn = $derived.by(() => {
		if (held) return run.approval.roles.length ? run.approval.roles.join(' or ') : 'an approver';
		if (run.status === 'needs_attention' || run.can_retry) return 'You';
		if (run.status === 'waiting')
			return providerLabel(run.attention?.provider ?? run.account?.custodian);
		if (run.status === 'running') return 'The worker';
		return 'Nobody — it has finished';
	});

	// The case as the Reviews queue reads it: the one body and the one set of words for a held
	// run, whatever seat is looking at it.
	const asReview = $derived(reviewRowFromRun(run));
	const eligible = $derived(run.approval.eligible_approvers ?? null);
	const stuck = $derived(held && eligible === 0);
	const heldFor = $derived.by(() => {
		const since = run.approval.held_since ?? run.requested_at;
		if (!held || !since) return null;
		const ms = now - new Date(since).getTime();
		return { label: waitingLabel(ms), tone: waitingTone(ms) };
	});
	// A review run draws its now-vs-proposed body; a provider run its recorded input, as facts.
	const showChange = $derived(run.is_review && !!run.change);
	const inputRows = $derived(showChange ? [] : reviewInputFacts(run.input).slice(0, 4));
	const SECTION = 'text-muted-foreground mb-1.5 text-[11.5px] font-semibold';
</script>

<Card glass class="flex h-full min-h-0 flex-col">
	<CardContent class="flex min-h-0 flex-1 flex-col p-0">
		<div class="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
			<div class="space-y-2">
				<div class="flex items-center justify-between gap-3">
					<span class="flex min-w-0 items-center gap-2">
						{#if run.case_no != null}
							<span
								class="border-warning-line bg-warning-subtle text-warning-strong rounded border px-1.5 font-mono text-xs font-semibold"
								>RV-{run.case_no}</span
							>
						{/if}
						<RecordId label="Run" value={run.uid} class="max-w-full" />
					</span>
					<div class="flex shrink-0 items-center gap-1.5">
						<StatusBadge tone={runStatusTone(run.status)} pulse={run.status === 'running'}>
							{runStatusLabel(run.status)}
						</StatusBadge>
						{#if onclose}
							<Button
								variant="ghost"
								size="icon"
								class="text-muted-foreground hover:text-foreground -my-1 -mr-1.5 size-8"
								aria-label="Close panel"
								title="Close (Esc)"
								onclick={onclose}
							>
								<XIcon class="size-4" />
							</Button>
						{/if}
					</div>
				</div>
				<p class="text-sm leading-snug">
					<span class="font-semibold">{run.name}</span>
					{#if run.contact}
						<span class="text-muted-foreground"> · </span>
						{#if links.client}
							<a href={links.client(run.contact.uid)} class="font-medium {LINK_RECORD}"
								>{run.contact.name ?? run.contact.uid}</a
							>
						{:else}<span class="font-medium">{run.contact.name ?? run.contact.uid}</span>{/if}
					{/if}
					{#if run.account}
						<span class="text-muted-foreground"> · </span>
						{#if links.account}
							<a href={links.account(run.account.uid)} class="font-medium {LINK_RECORD}"
								>{run.account.product_type ?? run.account.name ?? run.account.uid}</a
							>
						{:else}<span class="font-medium"
								>{run.account.product_type ?? run.account.name ?? run.account.uid}</span
							>{/if}
					{/if}
					{#if held && run.approval.held_uid && run.approval.held_uid !== run.uid && run.approval.held_name}
						<span class="text-muted-foreground"> · held at </span><span class="font-medium"
							>{run.approval.held_name}</span
						>
					{/if}
				</p>
				<div class="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
					<span class="inline-flex items-center gap-1.5">
						<LandmarkIcon class="size-3 shrink-0" />{providerLabel(
							run.account?.custodian ?? run.attention?.provider
						)}
					</span>
					<span
						>Raised by <span class="text-foreground"
							>{run.requested_by_name ?? (run.journey ? 'the journey' : 'system')}</span
						>
						· {relativeTime(run.requested_at, now)}</span
					>
					{#if heldFor}
						<span
							class="tabular-nums {heldFor.tone === 'danger'
								? 'text-destructive-strong font-medium'
								: heldFor.tone === 'warning'
									? 'text-warning-strong font-medium'
									: ''}">Held {heldFor.label}</span
						>
					{:else}
						<span class="tabular-nums">{formatDuration(runDurationMs(run, now))}</span>
					{/if}
				</div>
				{#if inputRows.length}
					<dl class="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
						{#each inputRows as fact (fact.label)}
							<dt class="text-muted-foreground truncate">{fact.label}</dt>
							<dd class="min-w-0 truncate {fact.money ? 'tabular-nums' : ''}">{fact.value}</dd>
						{/each}
					</dl>
				{/if}
			</div>

			{#if run.attention}
				<div class="bg-warning-subtle border-warning-line rounded-md border px-3 py-2 text-xs">
					<p class="text-warning-strong font-semibold">
						{run.attention.headline ?? `${run.attention.node_name} did not complete`}
					</p>
					{#if run.attention.detail}<p class="mt-0.5">{run.attention.detail}</p>{/if}
					<p class="text-muted-foreground mt-0.5">
						At {run.attention.node_name}{#if run.attention.status_code != null}
							· {run.attention.status_code}{/if}{#if run.attention.transient === false}
							· not transient{/if}
					</p>
				</div>
			{:else if held && run.justification}
				<p class="bg-muted/40 rounded-md px-3 py-2 text-xs">“{run.justification}”</p>
			{/if}

			{#if showChange}
				<!-- The change, as the Reviews queue draws it — so the ops seat approves what it can see. -->
				<div>
					<h3 class={SECTION}>The change</h3>
					<ReviewChange
						review={asReview}
						showClient={false}
						showAccount={false}
						showTitle={false}
					/>
				</div>
			{/if}

			{#if held}
				<div>
					<h3 class={SECTION}>Who has it</h3>
					<dl class="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Waiting on</dt>
						<dd>
							{waitingOn}{#if eligible != null}
								<span class="text-muted-foreground">
									{` · ${eligible} ${eligible === 1 ? 'person' : 'people'} other than the requester`}</span
								>{/if}
						</dd>
						<dt class="text-muted-foreground">You</dt>
						<dd
							class={run.approval.can_decide
								? 'text-primary'
								: stuck
									? 'text-destructive-strong'
									: ''}
						>
							{#if run.approval.can_decide}
								You can decide this one.
							{:else if stuck}
								Nobody else holds {run.approval.roles.join(' or ') || 'an approver role'} — it cannot
								be decided as things stand.
							{:else}
								Not yours to decide.
							{/if}
						</dd>
						<dt class="text-muted-foreground">Reminded</dt>
						<dd>
							{#if run.approval.reminded_at}
								{relativeTime(run.approval.reminded_at, now)}{#if (run.approval.reminders ?? 0) > 1}
									<span class="text-muted-foreground"> · {run.approval.reminders} times</span>{/if}
							{:else}
								<span class="text-muted-foreground"
									>Not yet · the request notice went out when it was held</span
								>
							{/if}
						</dd>
						{#if links.policy}
							<dt class="text-muted-foreground">Policy</dt>
							<dd>
								<a href={links.policy(run.action_key)} class="text-primary font-mono"
									>{run.action_key}</a
								>
							</dd>
						{/if}
					</dl>
				</div>
			{:else}
				<!-- The saga rail: label and who has it, as the transfers panel draws a transfer. -->
				<ol class="flex flex-col" aria-label="Progress">
					{#each rail as node, index (node.uid)}
						{@const last = index === rail.length - 1}
						{@const call =
							node.attempts.at(-1) ?? node.children.flatMap((c) => c.attempts).at(-1) ?? null}
						<li class="flex gap-2.5">
							<span class="flex w-3 flex-col items-center">
								<span class="mt-1.5 size-1.5 shrink-0 rounded-full {stepToneClass(node.status)}"
								></span>
								{#if !last}<span class="bg-border w-px flex-1"></span>{/if}
							</span>
							<span class="flex min-w-0 flex-col {last ? '' : 'pb-3'}">
								<span
									class="text-xs font-semibold {node.state.type === 'failed'
										? 'text-warning-strong'
										: ''}">{node.name}</span
								>
								<span class="text-muted-foreground truncate text-[10.5px]">
									{providerLabel(node.provider ?? node.children.find((c) => c.provider)?.provider)}
									{#if call?.method && call.url}· {call.method}
										{pathOf(call.url)}{#if call.status_code != null}
											· {call.status_code}{/if}
									{:else if node.state.message && node.state.type !== 'completed'}· {node.state
											.message}
									{:else if node.kind === 'gate'}· {node.approver_roles.join(', ') ||
											'approval'}{/if}
									· {formatDuration(nodeDurationMs(node, now))}
								</span>
							</span>
						</li>
					{/each}
				</ol>

				<div>
					<h3 class={SECTION}>Who has it</h3>
					<dl class="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
						<dt class="text-muted-foreground">Waiting on</dt>
						<dd>
							{waitingOn}{#if next.hint && run.status !== 'succeeded'}<span
									class="text-muted-foreground"
								>
									— {next.hint}</span
								>{/if}
						</dd>
						<dt class="text-muted-foreground">Approval</dt>
						<dd>
							{run.approval.required ? run.approval.roles.join(', ') || 'required' : 'not required'}
						</dd>
						{#if links.policy}
							<dt class="text-muted-foreground">Policy</dt>
							<dd>
								<a href={links.policy(run.action_key)} class="text-primary font-mono"
									>{run.action_key}</a
								>
							</dd>
						{/if}
					</dl>
				</div>
			{/if}

			{#if recent.length}
				<div>
					<h3 class={SECTION}>Timeline</h3>
					<dl class="grid grid-cols-[3.6rem_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs">
						{#each recent as event (event.id)}
							<dt class="text-muted-foreground tabular-nums">{clockLabel(event.at)}</dt>
							<dd>
								{eventLabel(event.type)}{#if event.run_name && event.run_uid !== run.uid}
									<span class="text-muted-foreground">
										· {event.run_name}</span
									>{/if}{#if event.actor_name}
									<span class="text-muted-foreground"> · {event.actor_name}</span>{/if}
							</dd>
						{/each}
					</dl>
				</div>
			{/if}
		</div>

		<div class="border-border/60 flex flex-wrap items-center gap-2 border-t p-3">
			{#if showDecide && enhance && actions.decide}
				<DecideDialogs
					runUid={run.approval.held_uid ?? run.uid}
					isReview={run.is_review}
					action={actions.decide}
					{enhance}
					approve={reviewApproveCopy(asReview)}
					reject={reviewRejectCopy(asReview)}
					approveClass="flex-1"
					ondecided={() => onrefresh?.()}
				/>
			{:else if showRetry && enhance}
				<form method="POST" action={actions.retry} use:enhance={after('Retrying')} class="flex-1">
					<input type="hidden" name="run_uid" value={run.uid} />
					<Button type="submit" class="w-full"><RotateCcwIcon class="size-4" />{next.label}</Button>
				</form>
			{/if}
			{#if showRemind && enhance}
				<form method="POST" action={actions.remind} use:enhance={after('Approvers reminded')}>
					<input type="hidden" name="run_uid" value={run.approval.held_uid ?? run.uid} />
					<Button type="submit" variant="outline" title="Send the approvers their notice again">
						<BellIcon class="size-4" />Remind
					</Button>
				</form>
			{/if}
			<Button
				href={links.run(run.uid)}
				variant={showDecide || showRetry ? 'outline' : 'default'}
				class={showDecide || showRetry ? '' : 'flex-1'}
			>
				Open run
			</Button>
			{#if showCancel}
				<Button
					type="button"
					variant="ghost"
					class="text-destructive"
					onclick={() => (cancelOpen = true)}>{held ? 'Cancel run' : 'Cancel'}</Button
				>
			{/if}
		</div>
	</CardContent>
</Card>

{#if showCancel && enhance}
	<form
		bind:this={cancelForm}
		method="POST"
		action={actions.cancel}
		class="hidden"
		use:enhance={after('Run cancelled')}
	>
		<input type="hidden" name="run_uid" value={run.uid} />
	</form>
	<ConfirmDialog
		bind:open={cancelOpen}
		title="Cancel this run?"
		description={held
			? 'The held request is withdrawn without a decision: nothing is sent or applied, and the person who raised it will need to submit it again.'
			: 'Anything still waiting on a provider stops; what already went through is kept. This cannot be undone.'}
		confirmLabel="Cancel run"
		cancelLabel="Keep it"
		destructive
		onconfirm={() => cancelForm?.requestSubmit()}
	/>
{/if}
