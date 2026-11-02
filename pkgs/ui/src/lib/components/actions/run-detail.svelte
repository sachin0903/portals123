<script lang="ts" module>
	/** The form actions the mounting page wires. Each is optional; an absent one hides its control,
	 *  which is how a read-only mount (the admin portal over a firm) stays read-only. */
	export type RunDetailActions = {
		/** Retry the whole run from where it stopped (`?/retry`). */
		retry?: string;
		/** Retry one node (`?/retryStep`, posting `step_id`). */
		retryStep?: string;
		/** Cancel the run (`?/cancel`). */
		cancel?: string;
		/** Decide a held run (`?/decide`, posting `run_uid`, `decision`, `note`, `is_review`). */
		decide?: string;
		/** Send the approvers of a held run their notice again (`?/remind`, posting `run_uid`). */
		remind?: string;
	};
</script>

<script lang="ts">
	// One run, as its tree. The header names the action and links the records it is about; the
	// one next action sits at its right. A refused run says the provider's reason once, in words,
	// then the progress strip, then the tree, with the rail beside it: what happened and when,
	// who may decide, what it is about, how long it took, what went in.
	import { toast } from 'svelte-sonner';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import CircleAlertIcon from '@lucide/svelte/icons/circle-alert';
	import { REFRESH_WHILE } from '../notifications/poll-controller.js';
	import RefreshWhile from '../ui/refresh-while/refresh-while.svelte';
	import PageHeader from '../shell/page-header.svelte';
	import type { BackLink } from '../shell/types.js';
	import { Alert, AlertDescription, AlertTitle } from '../ui/alert/index.js';
	import { Button } from '../ui/button/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { CopyButton } from '../ui/copy-button/index.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { highlightJson } from '../audit/highlight-json.js';
	import { LINK_RECORD } from '../../text-shell.js';
	import type { Enhance } from '../book/clients/form-enhance.js';
	import ReviewChange from '../book/reviews/review-change.svelte';
	import {
		reviewApproveCopy,
		reviewInputFacts,
		reviewRejectCopy
	} from '../book/reviews/reviews.js';
	import DecideDialogs from './decide-dialogs.svelte';
	import RunNode from './run-node.svelte';
	import { nextAction } from './next-action.js';
	import { reviewRowFromRun } from './review-row.js';
	import type { RunDetailData, RunLinks } from './run-types.js';
	import {
		clockLabel,
		eventLabel,
		eventTone,
		formatDuration,
		nodeDurationMs,
		providerLabel,
		relativeTime,
		runDurationMs,
		runInFlight,
		runStatusLabel,
		runStatusTone,
		stepToneClass,
		untilTime,
		whenLabel
	} from './run-states.js';

	let {
		run,
		canManage = false,
		enhance,
		links,
		actions = {},
		back,
		onrefresh
	}: {
		run: RunDetailData;
		canManage?: boolean;
		enhance?: Enhance;
		links: RunLinks;
		actions?: RunDetailActions;
		/** The way back, when the app publishes none of its own (see `page-back.ts`). */
		back?: BackLink;
		/** Re-read while the run is in flight or resting on a provider. */
		onrefresh?: () => void;
	} = $props();

	const polling = $derived(!!onrefresh && runInFlight(run.status));
	// One clock for the whole page, so SSR and hydration agree and durations do not jitter.
	const now = Date.now();

	const next = $derived(nextAction(run));
	const canAct = $derived(canManage && !!enhance);
	const showRetry = $derived(canAct && next.kind === 'retry' && !!actions.retry);
	const showDecide = $derived(canAct && next.kind === 'decide' && !!actions.decide);
	const showCancel = $derived(canAct && next.canCancel && !!actions.cancel);
	const held = $derived(run.status === 'awaiting_approval');
	const showRemind = $derived(held && canAct && !!actions.remind);

	// The gates this viewer may decide in the tree: the open one, when the API says so — the
	// root's own, or the gate under a composite's gated child.
	const decidable = $derived(
		new Set(run.approval.can_decide && run.approval.gate_uid ? [run.approval.gate_uid] : [])
	);
	// The case as the Reviews queue reads it — one body, one set of words, on every seat.
	const asReview = $derived(reviewRowFromRun(run));
	const eligible = $derived(run.approval.eligible_approvers ?? null);

	let onlyProblems = $state(false);
	let expandAll = $state(false);

	/** The rows the tree draws: the children, or — for a leaf run — the run itself. */
	const nodes = $derived(run.children.length ? run.children : [run]);
	const strip = $derived(run.children.filter((child) => child.kind !== 'gate'));

	// The timeline is the run's LIFECYCLE, not a transcript: the root's own events, plus the
	// children's turning points (held, decided, parked, failed, cancelled, retried). A child being
	// created and completing is the tree's business — nine leaves would make twenty lines of it.
	// A burst of the same word at the same second (a refused leaf, its parent and the root all go
	// "needs attention" together) folds to one line.
	const ROUTINE = new Set(['run.created', 'run.completed']);
	const timeline = $derived.by(() => {
		const out: typeof run.events = [];
		for (const event of run.events) {
			if (event.run_uid !== run.uid && ROUTINE.has(event.type)) continue;
			const prev = out.at(-1);
			if (prev && prev.type === event.type && prev.at === event.at) continue;
			out.push(event);
		}
		return out;
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

	function lines(value: unknown): { text: string; cls: string }[][] | null {
		if (value == null) return null;
		const text = JSON.stringify(value, null, 2);
		return text ? highlightJson(text) : null;
	}

	/** The recorded input as the Reviews queue prints it — one renderer, so money, dates and the
	 *  form's labels read the same here as on the case. Ops re-key a refused transfer from this
	 *  panel, so a list of documents or assets is one line per entry. */
	const inputFacts = $derived(reviewInputFacts(run.input));

	const subjectProvider = $derived(run.account?.custodian ?? run.attention?.provider ?? null);
	const duration = $derived(formatDuration(runDurationMs(run, now)));
	const SECTION = 'text-[0.8125rem] leading-5 font-semibold';
</script>

<RefreshWhile active={polling} interval={REFRESH_WHILE.actionRun} onpoll={() => onrefresh?.()} />

{#snippet jsonBlock(rows: { text: string; cls: string }[][])}
	<div class="max-h-64 overflow-auto font-mono text-[11px] leading-normal">
		{#each rows as segs, i (i)}
			<div class="whitespace-pre">
				{#each segs as seg, j (j)}<span class={seg.cls}>{seg.text}</span>{/each}
			</div>
		{/each}
	</div>
{/snippet}

{#snippet headerActions()}
	{#if showCancel}
		<Button type="button" variant="ghost" onclick={() => (cancelOpen = true)}>Cancel run</Button>
	{/if}
	<CopyButton value={run.uid} label="Copy run id" size="md" />
	{#if showRemind && enhance}
		<form method="POST" action={actions.remind} use:enhance={after('Approvers reminded')}>
			<input type="hidden" name="run_uid" value={run.approval.held_uid ?? run.uid} />
			<Button type="submit" variant="outline">Remind approvers</Button>
		</form>
	{/if}
	{#if showDecide && enhance && actions.decide}
		<DecideDialogs
			runUid={run.approval.held_uid ?? run.uid}
			isReview={run.is_review}
			action={actions.decide}
			{enhance}
			approve={reviewApproveCopy(asReview)}
			reject={reviewRejectCopy(asReview)}
			ondecided={() => onrefresh?.()}
		/>
	{:else if showRetry && enhance}
		<form method="POST" action={actions.retry} use:enhance={after('Retrying')}>
			<input type="hidden" name="run_uid" value={run.uid} />
			<Button type="submit"><RotateCcwIcon class="size-4" />{next.label}</Button>
		</form>
	{/if}
{/snippet}

<PageHeader eyebrow="Run" title={run.name} {back} actions={headerActions} class="mb-3" />

<div class="flex flex-col gap-5">
	<!-- Who and what this run is about, with the records linked. -->
	<p class="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-sm">
		<StatusBadge tone={runStatusTone(run.status)} pulse={run.status === 'running'}>
			{runStatusLabel(run.status)}
		</StatusBadge>
		{#if run.case_no != null}
			<span
				class="border-warning-line bg-warning-subtle text-warning-strong rounded border px-1.5 font-mono text-xs font-semibold"
				>RV-{run.case_no}</span
			>
		{/if}
		<span class="text-muted-foreground font-mono text-xs">{run.action_key}</span>
		<span class="text-muted-foreground font-mono text-xs">{run.uid}</span>
		{#if run.contact}
			<span class="text-muted-foreground">for</span>
			{#if links.client}
				<a href={links.client(run.contact.uid)} class="font-medium {LINK_RECORD}"
					>{run.contact.name ?? run.contact.uid}</a
				>
			{:else}
				<span class="font-medium">{run.contact.name ?? run.contact.uid}</span>
			{/if}
		{/if}
		{#if run.account}
			<span class="text-muted-foreground">·</span>
			{#if links.account}
				<a href={links.account(run.account.uid)} class="font-medium {LINK_RECORD}">
					{[run.account.product_type, run.account.name ?? run.account.uid]
						.filter(Boolean)
						.join(' · ')}
				</a>
			{:else}
				<span class="font-medium"
					>{[run.account.product_type, run.account.name ?? run.account.uid]
						.filter(Boolean)
						.join(' · ')}</span
				>
			{/if}
		{:else if run.subject_kind && run.subject_uid && !run.contact}
			<span class="text-muted-foreground">for</span>
			<span class="font-medium"
				>{run.subject_kind} <span class="font-mono text-xs">{run.subject_uid}</span></span
			>
		{/if}
		{#if subjectProvider}
			<span
				class="text-muted-foreground border-border/70 rounded border px-1.5 text-[10.5px] font-semibold tracking-wide"
			>
				{providerLabel(subjectProvider)}
			</span>
		{/if}
		<span class="text-muted-foreground">·</span>
		{#if run.journey}
			<span class="text-muted-foreground">raised by the journey step</span>
			{#if links.journey}
				<a href={links.journey(run.journey.application_uid)} class="font-medium {LINK_RECORD}"
					>{run.journey.step_title ?? 'Onboarding'}</a
				>
			{:else}
				<span class="font-medium">{run.journey.step_title ?? 'Onboarding'}</span>
			{/if}
		{:else}
			<span class="text-muted-foreground">requested by</span>
			<span class="font-medium">{run.requested_by_name ?? 'system'}</span>
		{/if}
		<span class="text-muted-foreground">{relativeTime(run.requested_at, now)}</span>
	</p>

	<!-- The provider's reason, once, in words — and what the one next action will do. -->
	{#if run.attention}
		<Alert variant={run.status === 'needs_attention' ? 'warning' : 'destructive'}>
			<TriangleAlertIcon class="size-4" />
			<AlertTitle>
				{run.attention.headline ?? `${run.attention.node_name} did not complete`}
			</AlertTitle>
			<AlertDescription>
				<span class="flex flex-col gap-1">
					{#if run.attention.detail}<span>{run.attention.detail}</span>{/if}
					<span class="flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
						<span
							>At <b class="font-medium">{run.attention.node_name}</b> · {providerLabel(
								run.attention.provider
							)}{#if run.attention.status_code != null}
								· {run.attention.status_code}{/if}</span
						>
						{#if run.attention.code}<span class="font-mono">{run.attention.code}</span>{/if}
						{#if run.attention.tracking_id}<span
								>Tracking <span class="font-mono">{run.attention.tracking_id}</span></span
							>{/if}
						{#if run.attention.transient === false}<span
								>Not transient — fix the cause, then retry.</span
							>
						{:else if run.attention.transient === true}<span
								>Transient — a retry may simply succeed.</span
							>{/if}
					</span>
					{#if next.hint}<span class="text-xs">{next.hint}</span>{/if}
				</span>
			</AlertDescription>
		</Alert>
	{:else if run.status === 'awaiting_approval'}
		<Alert variant="warning">
			<CircleAlertIcon class="size-4" />
			<AlertTitle>
				Held for approval{#if run.approval.held_name && run.approval.held_uid !== run.uid}
					· at {run.approval.held_name}{/if}
			</AlertTitle>
			<AlertDescription>
				<span class="flex flex-col gap-1">
					{#if run.justification}<span>“{run.justification}”</span>{/if}
					{#if eligible === 0}
						<span class="text-xs font-medium">
							Nobody else holds {run.approval.roles.join(' or ') || 'an approver role'}, so this
							cannot be decided as things stand. Ask an administrator to give someone that role.
						</span>
					{:else}
						<span class="text-xs">{next.hint}</span>
					{/if}
				</span>
			</AlertDescription>
		</Alert>
	{:else if next.hint}
		<p class="text-muted-foreground text-xs">{next.hint}</p>
	{/if}

	<!-- Progress: one tile per immediate child, with who did it. The listing's step bar, legible. -->
	{#if strip.length > 1}
		<section
			aria-label="Progress"
			class="grid gap-2.5"
			style="grid-template-columns: repeat({Math.min(strip.length, 6)}, minmax(0, 1fr))"
		>
			{#each strip as child, index (child.uid)}
				<a
					href="#node-{child.uid}"
					class="bg-card border-border/60 hover:bg-muted/40 flex min-w-0 flex-col gap-1 rounded-xl border p-3 text-xs"
				>
					<span class="text-muted-foreground truncate font-medium">{index + 1} · {child.name}</span>
					<span class="flex items-center justify-between gap-2">
						<StatusBadge tone={runStatusTone(child.status)} class="max-w-full min-w-0 truncate"
							>{runStatusLabel(child.status)}</StatusBadge
						>
						<span class="text-muted-foreground shrink-0 tabular-nums"
							>{formatDuration(nodeDurationMs(child, now))}</span
						>
					</span>
					<span class="text-muted-foreground/80 truncate text-[10.5px] font-medium">
						{providerLabel(
							child.provider ?? child.children.find((c) => c.provider)?.provider
						)}{#if child.state.message && child.state.type !== 'completed'}
							· {child.state.message}{/if}
					</span>
				</a>
			{/each}
		</section>
	{/if}

	<div class="flex flex-wrap items-start gap-5">
		<!-- The tree -->
		<section
			class="bg-card border-border/60 min-w-0 flex-[999_1_36rem] overflow-hidden rounded-xl border"
			aria-label="What ran"
		>
			<div
				class="border-border/60 flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3"
			>
				<h2 class={SECTION}>
					What ran
					<span class="text-muted-foreground/70 ml-1.5 font-normal tabular-nums">
						{run.children.length
							? `${run.children.length} ${run.children.length === 1 ? 'child' : 'children'} · `
							: ''}{run.timing.calls}
						{run.timing.calls === 1 ? 'call' : 'calls'} · {duration}
					</span>
				</h2>
				<div class="flex items-center gap-1">
					<Button
						type="button"
						size="sm"
						variant={expandAll ? 'secondary' : 'ghost'}
						onclick={() => (expandAll = !expandAll)}
					>
						{expandAll ? 'Collapse' : 'Expand all'}
					</Button>
					{#if run.can_retry || run.status === 'needs_attention'}
						<Button
							type="button"
							size="sm"
							variant={onlyProblems ? 'secondary' : 'ghost'}
							onclick={() => (onlyProblems = !onlyProblems)}
						>
							Only problems
						</Button>
					{/if}
				</div>
			</div>
			<div class="divide-border/60 flex flex-col divide-y">
				{#each nodes as node (node.uid)}
					<div id="node-{node.uid}">
						<RunNode
							{node}
							{canManage}
							{enhance}
							retryAction={actions.retryStep}
							decideAction={actions.decide}
							{decidable}
							{onlyProblems}
							{expandAll}
							{links}
							{now}
							parentUid={run.uid}
							isReview={run.is_review}
						/>
					</div>
				{:else}
					<p class="text-muted-foreground p-4 text-xs">Nothing has run yet.</p>
				{/each}
			</div>
		</section>

		<!-- The rail -->
		<aside
			class="flex min-w-0 flex-[1_1_20rem] flex-col gap-4 lg:max-w-sm"
			aria-label="About this run"
		>
			<section class="bg-card border-border/60 rounded-xl border p-4">
				<h2 class="{SECTION} mb-2.5">Timeline</h2>
				{#if timeline.length}
					<ol class="flex flex-col">
						{#each timeline as event, index (event.id)}
							{@const last = index === timeline.length - 1}
							<li class="grid grid-cols-[3.6rem_0.75rem_minmax(0,1fr)] gap-x-2.5">
								<span class="text-muted-foreground pt-0.5 text-right text-[11px] tabular-nums"
									>{clockLabel(event.at)}</span
								>
								<span class="flex flex-col items-center">
									<span
										class="mt-1.5 size-1.5 shrink-0 rounded-full {stepToneClass(
											eventTone(event.type) === 'neutral'
												? 'cancelled'
												: eventTone(event.type) === 'success'
													? 'succeeded'
													: eventTone(event.type) === 'warning'
														? 'needs_attention'
														: eventTone(event.type) === 'danger'
															? 'failed'
															: 'running'
										)}"
									></span>
									{#if !last}<span class="bg-border w-px flex-1"></span>{/if}
								</span>
								<span class="flex flex-col {last ? '' : 'pb-3'} text-xs">
									<span class="font-medium"
										>{eventLabel(event.type)}{#if event.run_name && event.run_uid !== run.uid}
											<span class="text-muted-foreground font-normal">
												· {event.run_name}</span
											>{/if}</span
									>
									{#if event.actor_name || event.type === 'run.created' || event.type === 'run.held'}
										<span class="text-muted-foreground"
											>{event.actor_name ??
												(run.journey ? 'the journey' : (run.requested_by_name ?? 'system'))}</span
										>
									{/if}
								</span>
							</li>
						{/each}
					</ol>
				{:else}
					<p class="text-muted-foreground text-xs">No events recorded.</p>
				{/if}
			</section>

			{#if run.is_review && run.change}
				<!-- The change itself, as the Reviews queue draws it, so an approver here sees what
				     they are approving — a bank change shows its digits. -->
				<section class="bg-card border-border/60 rounded-xl border p-4">
					<h2 class="{SECTION} mb-2.5">The change</h2>
					<ReviewChange
						review={asReview}
						showClient={false}
						showAccount={false}
						showTitle={false}
					/>
				</section>
			{/if}

			<section class="bg-card border-border/60 rounded-xl border p-4">
				<h2 class="{SECTION} mb-2.5">Approval</h2>
				{#if run.approval.required}
					<dl class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
						<dt class="text-muted-foreground">Approvable by</dt>
						<dd>
							{run.approval.roles.length ? run.approval.roles.join(', ') : 'Any approver'}
							{#if held && eligible != null}
								<span
									class={eligible === 0
										? 'text-destructive-strong block'
										: 'text-muted-foreground block'}
								>
									{eligible === 0
										? 'Nobody else holds these roles.'
										: `${eligible} ${eligible === 1 ? 'person' : 'people'} other than the requester`}
								</span>
							{/if}
						</dd>
						{#if held}
							<dt class="text-muted-foreground">Reminded</dt>
							<dd>
								{#if run.approval.reminded_at}
									{whenLabel(run.approval.reminded_at)}{#if (run.approval.reminders ?? 0) > 1}
										<span class="text-muted-foreground">
											· {run.approval.reminders} times</span
										>{/if}
								{:else}
									<span class="text-muted-foreground">Not yet</span>
								{/if}
							</dd>
						{/if}
						{#if run.justification}
							<dt class="text-muted-foreground">Reason</dt>
							<dd>“{run.justification}”</dd>
						{/if}
						{#if run.decision}
							<dt class="text-muted-foreground">Decision</dt>
							<dd>
								{run.decision === 'approved' ? 'Approved' : 'Rejected'} by {run.decided_by_name ??
									'someone'}
								{#if run.decided_at}<span class="text-muted-foreground">
										· {whenLabel(run.decided_at)}</span
									>{/if}
								{#if run.decision_note}<span class="block">“{run.decision_note}”</span>{/if}
							</dd>
						{:else if run.status === 'awaiting_approval'}
							<dt class="text-muted-foreground">Status</dt>
							<dd>
								{run.approval.can_decide
									? 'Waiting — you can decide this one.'
									: 'Waiting for a decision.'}
							</dd>
						{/if}
					</dl>
				{:else}
					<p class="text-muted-foreground text-xs">
						Not required. The firm's policy on <span class="font-mono">{run.action_key}</span> names
						no approver.
						{#if links.policy}<a
								href={links.policy(run.action_key)}
								class="text-primary ml-1 font-medium">Open policy</a
							>{/if}
					</p>
				{/if}
			</section>

			<section class="bg-card border-border/60 rounded-xl border p-4">
				<h2 class="{SECTION} mb-2.5">Context</h2>
				<dl class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
					{#if run.contact}
						<dt class="text-muted-foreground">Client</dt>
						<dd>
							{#if links.client}<a
									href={links.client(run.contact.uid)}
									class="font-medium {LINK_RECORD}">{run.contact.name ?? run.contact.uid}</a
								>{:else}{run.contact.name ?? run.contact.uid}{/if}
						</dd>
					{/if}
					{#if run.account}
						<dt class="text-muted-foreground">Account</dt>
						<dd>
							{#if links.account}<a
									href={links.account(run.account.uid)}
									class="font-medium {LINK_RECORD}"
									>{[run.account.product_type, run.account.name ?? run.account.uid]
										.filter(Boolean)
										.join(' · ')}</a
								>{:else}{[run.account.product_type, run.account.name ?? run.account.uid]
									.filter(Boolean)
									.join(' · ')}{/if}
							{#if run.account.custodian}<span class="text-muted-foreground">
									· {providerLabel(run.account.custodian)}</span
								>{/if}
						</dd>
					{/if}
					{#if run.journey}
						<dt class="text-muted-foreground">Journey</dt>
						<dd>
							{#if links.journey}<a
									href={links.journey(run.journey.application_uid)}
									class="font-medium {LINK_RECORD}">{run.journey.step_title ?? 'Onboarding'}</a
								>{:else}{run.journey.step_title ?? 'Onboarding'}{/if}
						</dd>
					{/if}
					<dt class="text-muted-foreground">Requested by</dt>
					<dd>{run.requested_by_name ?? 'system'}</dd>
					<dt class="text-muted-foreground">Run</dt>
					<dd class="flex items-center gap-1 font-mono">
						<span class="truncate">{run.uid}</span><CopyButton
							value={run.uid}
							label="Copy run id"
						/>
					</dd>
					{#if run.case_no != null}
						<dt class="text-muted-foreground">Case</dt>
						<dd class="font-mono">RV-{run.case_no}</dd>
					{/if}
					{#if run.manual}
						<dt class="text-muted-foreground">Manual</dt>
						<dd>Completed by hand when accepted; no provider call.</dd>
					{/if}
				</dl>
			</section>

			<section class="bg-card border-border/60 rounded-xl border p-4">
				<h2 class="{SECTION} mb-2.5">Timing</h2>
				<dl class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs tabular-nums">
					<dt class="text-muted-foreground">Requested</dt>
					<dd>{whenLabel(run.timing.requested_at)}</dd>
					<dt class="text-muted-foreground">Started</dt>
					<dd>{run.timing.started_at ? clockLabel(run.timing.started_at) : 'Not yet'}</dd>
					<dt class="text-muted-foreground">{run.timing.ended_at ? 'Ended' : 'Running for'}</dt>
					<dd>
						{run.timing.ended_at ? `${clockLabel(run.timing.ended_at)} · ${duration}` : duration}
					</dd>
					{#if run.timing.next_poll_at}
						<dt class="text-muted-foreground">Next poll</dt>
						<dd>{untilTime(run.timing.next_poll_at, now)}</dd>
					{/if}
					<dt class="text-muted-foreground">Passes</dt>
					<dd>
						{run.timing.passes}
						<span class="text-muted-foreground"
							>· {run.timing.calls}
							{run.timing.calls === 1 ? 'call' : 'calls'} across {run.timing.leaves}
							{run.timing.leaves === 1 ? 'leaf' : 'leaves'}</span
						>
					</dd>
				</dl>
			</section>

			{#if run.input || Object.keys(run.params).length}
				<section class="bg-card border-border/60 rounded-xl border p-4">
					<div class="mb-2.5 flex items-center justify-between gap-2">
						<div class="flex items-baseline gap-2">
							<h2 class={SECTION}>Input</h2>
							{#if run.input}<span class="text-muted-foreground text-[11px]"
									>what the door recorded</span
								>{/if}
						</div>
						{#if run.input}
							<!-- The whole form, for setting the request up by hand elsewhere when the provider
							     refused it: one click, every field. -->
							<CopyButton value={JSON.stringify(run.input, null, 2)} label="Copy input" />
						{/if}
					</div>
					{#if inputFacts.length}
						<dl class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-xs">
							{#each inputFacts as fact (fact.label)}
								{@const lines = fact.value.split('; ')}
								<dt class="text-muted-foreground truncate">{fact.label}</dt>
								<dd class="min-w-0 break-words {fact.money ? 'tabular-nums' : ''}">
									{#if lines.length === 1}
										{fact.value}
									{:else}
										<ul class="space-y-0.5">
											{#each lines as line, i (i)}<li>{line}</li>{/each}
										</ul>
									{/if}
								</dd>
							{/each}
						</dl>
					{/if}
					{#if Object.keys(run.params).length}
						<details class="group {run.input ? 'mt-3' : ''}">
							<summary
								class="text-muted-foreground hover:text-foreground cursor-pointer list-none text-xs"
							>
								<span class="group-open:hidden">Show raw params</span><span
									class="hidden group-open:inline">Raw params</span
								>
							</summary>
							<div class="bg-muted/40 mt-2 rounded-md p-2">
								{@render jsonBlock(lines(run.params) ?? [])}
							</div>
						</details>
					{/if}
				</section>
			{/if}

			{#if run.result != null && lines(run.result) && run.state.type === 'completed'}
				<section class="bg-card border-border/60 rounded-xl border p-4">
					<div class="mb-2.5 flex items-center justify-between gap-2">
						<h2 class={SECTION}>Output</h2>
						<CopyButton value={JSON.stringify(run.result, null, 2)} label="Copy output" />
					</div>
					{@render jsonBlock(lines(run.result) ?? [])}
				</section>
			{/if}
		</aside>
	</div>
</div>

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
		description={run.status === 'awaiting_approval'
			? 'The held instruction will not be sent, and its booking is marked rejected so nothing sends it later.'
			: 'Anything still waiting on a provider stops; what already went through is kept. This cannot be undone.'}
		confirmLabel="Cancel run"
		cancelLabel="Keep it"
		destructive
		onconfirm={() => cancelForm?.requestSubmit()}
	/>
{/if}
