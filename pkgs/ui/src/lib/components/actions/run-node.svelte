<script lang="ts">
	// One node of a run's tree, recursively: the run, its calls, its result and its children.
	//
	// The row leads with the action's NAME (every action declares one), with the key beside it in
	// mono for the engineer. A composite opens onto its children; a leaf opens onto its attempts,
	// each attempt onto its request and response. Every state baton can leave a node in has a face
	// here (the States board on the design canvas): a wait is a wait, a skip says why, a gate says
	// who may decide. The one control on a row is the one thing a person can do to that node.
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import { toast } from 'svelte-sonner';
	import { Button } from '../ui/button/index.js';
	import { CopyButton } from '../ui/copy-button/index.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { highlightJson } from '../audit/highlight-json.js';
	import type { Enhance } from '../book/clients/form-enhance.js';
	import DecideDialogs from './decide-dialogs.svelte';
	import type { RunAttempt, RunLinks, RunNodeData } from './run-types.js';
	import {
		attemptOutcomeLabel,
		attemptOutcomeTone,
		clockLabel,
		formatDuration,
		httpTone,
		nodeDurationMs,
		pathOf,
		providerLabel,
		runCanRetry,
		runStatusLabel,
		runStatusTone,
		untilTime
	} from './run-states.js';
	import RunNode from './run-node.svelte';

	let {
		node,
		canManage = false,
		enhance,
		retryAction,
		decideAction,
		decidable = new Set<string>(),
		onlyProblems = false,
		expandAll = false,
		links,
		now = Date.now(),
		parentUid = null,
		isReview = false
	}: {
		node: RunNodeData;
		canManage?: boolean;
		enhance?: Enhance;
		/** The form action that retries ONE node (`?/retryStep`). Absent ⇒ no per-node retry. */
		retryAction?: string;
		/** The form action that decides a gate (`?/decide`). Absent ⇒ gates are read-only. */
		decideAction?: string;
		/** The gate uids this viewer may decide (advisory; the engine checks again). */
		decidable?: Set<string>;
		/** The run this node sits under. A GATE is decided by posting the uid of the run it holds
		 *  — its parent — not the gate's own: the engine looks for the open gate under the run it
		 *  is given. */
		parentUid?: string | null;
		/** The root is a book review: its decision goes through the reviews path. */
		isReview?: boolean;
		/** Show only the nodes on a path to a problem; the rest fold to a one-line count. */
		onlyProblems?: boolean;
		expandAll?: boolean;
		links?: RunLinks;
		/** The clock the durations are read against — passed in so SSR and the browser agree. */
		now?: number;
	} = $props();

	const isGate = $derived(node.kind === 'gate');
	const isComposite = $derived(node.children.length > 0);
	const problem = $derived(node.state.type === 'failed' || node.state.type === 'crashed');
	const live = $derived(
		node.state.type === 'running' ||
			node.state.type === 'scheduled' ||
			node.state.type === 'paused' ||
			node.state.type === 'pending'
	);
	/** A node worth opening on arrival: anything a person should look at, and every composite. */
	const interesting = $derived(problem || live || (isComposite && node.depth < 2));

	let manual = $state<boolean | null>(null);
	const open = $derived(manual ?? (expandAll || interesting));
	let openCalls = $state<Set<number>>(new Set());
	function toggleCall(no: number): void {
		const next = new Set(openCalls);
		if (next.has(no)) next.delete(no);
		else next.add(no);
		openCalls = next;
	}

	/** Under "only problems", a healthy composite folds; a healthy leaf hides altogether. */
	const hasProblemBelow = $derived.by(() => {
		const walk = (n: RunNodeData): boolean =>
			n.state.type === 'failed' || n.state.type === 'crashed' || n.children.some(walk);
		return walk(node);
	});

	const duration = $derived(formatDuration(nodeDurationMs(node, now)));
	const lastAttempt = $derived(node.attempts.at(-1) ?? null);

	/** The one line under the name: what the node is doing or did, in words. */
	const meta = $derived.by(() => {
		const parts: string[] = [];
		if (isGate) {
			if (node.state.type === 'paused') {
				parts.push(
					node.approver_roles.length
						? `Approvable by ${node.approver_roles.join(', ')}`
						: 'Approval required'
				);
			} else if (node.decision) {
				parts.push(
					`${node.decision === 'approved' ? 'Approved' : 'Rejected'} by ${
						node.decided_by_name ?? 'someone'
					}`
				);
			}
			return parts.join(' · ');
		}
		if (node.state.type === 'scheduled' && node.state.name === 'Waiting') {
			parts.push(node.state.message ?? 'Waiting on the provider');
			if (node.scheduled_at) parts.push(`polling again ${untilTime(node.scheduled_at, now)}`);
		} else if (node.state.type === 'scheduled' && node.state.name === 'AwaitingRetry') {
			parts.push(node.state.message ?? 'Transient failure');
			if (node.retries != null)
				parts.push(
					`retry ${node.retries_used + 1} of ${node.retries} ${untilTime(node.scheduled_at, now)}`
				);
		} else if (node.state.name === 'Skipped') {
			parts.push(node.state.message ?? 'Skipped');
		} else if (node.state.type === 'paused' && node.state.name === 'AwaitingChildren') {
			parts.push('Waiting on its children');
		} else if (node.state.type === 'crashed') {
			parts.push('The lease lapsed mid-attempt');
		}
		if (node.attempts.length > 1) parts.push(`${node.attempts.length} attempts`);
		if (node.on_failure === 'continue') parts.push('best effort');
		if (isComposite && node.state.type !== 'failed')
			parts.push(`${node.children.length} ${node.children.length === 1 ? 'child' : 'children'}`);
		return parts.join(' · ');
	});

	/** The collapsed summary at the right of a leaf row: the last call's verb and path, or what a
	 *  book read decided. Never "No address recorded" — a function leaf has a result, not an address. */
	const summary = $derived.by(() => {
		if (isGate || isComposite) return null;
		if (lastAttempt?.url) return { kind: 'call' as const, attempt: lastAttempt };
		if (node.result && typeof node.result === 'object' && !Array.isArray(node.result)) {
			const entries = Object.entries(node.result as Record<string, unknown>)
				.filter(([, v]) => v == null || ['string', 'number', 'boolean'].includes(typeof v))
				.slice(0, 3)
				.map(([k, v]) => `${k.replace(/_/g, ' ')} ${v == null ? '—' : String(v)}`);
			if (entries.length) return { kind: 'result' as const, text: entries.join(' · ') };
		}
		if (typeof node.result === 'string') return { kind: 'result' as const, text: node.result };
		return null;
	});

	/** A provider call, as against a function leaf whose two slots are its input and output. */
	const isCall = (attempt: RunAttempt): boolean =>
		attempt.kind ? attempt.kind === 'call' : Boolean(attempt.method && attempt.url);

	function lines(value: unknown): { text: string; cls: string }[][] | null {
		if (value == null) return null;
		const text = JSON.stringify(value, null, 2);
		return text ? highlightJson(text) : null;
	}
	function raw(value: unknown): string {
		return value == null ? '' : JSON.stringify(value, null, 2);
	}

	const canRetryHere = $derived(
		canManage && !!enhance && !!retryAction && !isGate && runCanRetry(node.status) && !isComposite
	);
	const canDecideHere = $derived(
		canManage &&
			!!enhance &&
			!!decideAction &&
			isGate &&
			node.state.type === 'paused' &&
			decidable.has(node.uid)
	);

	function after(verb: string) {
		return () =>
			async ({
				result,
				update
			}: {
				result: { type: string; data?: { error?: string } };
				update: (opts?: { reset?: boolean }) => Promise<void>;
			}) => {
				if (result.type === 'success') toast.success(verb);
				else
					toast.error(
						String((result.type === 'failure' && result.data?.error) || 'That did not go through.')
					);
				await update({ reset: false });
			};
	}

	const dotClass = $derived.by(() => {
		switch (runStatusTone(node.status)) {
			case 'success':
				return 'bg-success';
			case 'warning':
				return 'bg-warning ring-[3px] ring-warning/20';
			case 'danger':
				return 'bg-destructive ring-[3px] ring-destructive/20';
			case 'info':
				return live ? 'bg-info animate-pulse' : 'bg-info';
			default:
				return 'bg-muted-foreground/50';
		}
	});
</script>

{#snippet jsonBlock(rows: { text: string; cls: string }[][])}
	<div class="max-h-64 overflow-auto font-mono text-[11px] leading-normal">
		{#each rows as segs, i (i)}
			<div class="whitespace-pre">
				{#each segs as seg, j (j)}<span class={seg.cls}>{seg.text}</span>{/each}
			</div>
		{/each}
	</div>
{/snippet}

{#snippet attemptRow(attempt: RunAttempt)}
	{@const request = lines(attempt.request)}
	{@const response = lines(attempt.response)}
	{@const callOpen = openCalls.has(attempt.attempt_no)}
	{@const hasBody = !!request || !!response}
	<div class="border-border/70 border-t first:border-t-0">
		<button
			type="button"
			class="hover:bg-muted/40 flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-left text-xs disabled:cursor-default"
			aria-expanded={callOpen}
			disabled={!hasBody}
			onclick={() => toggleCall(attempt.attempt_no)}
		>
			{#if node.attempts.length > 1}
				<span class="text-muted-foreground w-4 tabular-nums">{attempt.attempt_no}</span>
			{/if}
			{#if isCall(attempt) && attempt.url}
				<span class="font-mono font-medium">{attempt.method ?? 'Call'}</span>
				<span class="text-muted-foreground min-w-0 flex-1 truncate font-mono" title={attempt.url}>
					{pathOf(attempt.url)}
				</span>
			{:else}
				<StatusBadge tone={attemptOutcomeTone(attempt.outcome)}>
					{attemptOutcomeLabel(attempt.outcome)}
				</StatusBadge>
				<span class="min-w-0 flex-1 truncate" title={attempt.error ?? ''}>
					{attempt.error ?? (attempt.outcome === 'ok' ? 'Returned' : '')}
				</span>
			{/if}
			<span class="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1">
				{#if attempt.status_code != null}
					<StatusBadge tone={httpTone(attempt.status_code)}>{attempt.status_code}</StatusBadge>
				{:else if attempt.method && attempt.outcome !== 'ok'}
					<StatusBadge tone={attemptOutcomeTone(attempt.outcome)}>
						{attemptOutcomeLabel(attempt.outcome)}
					</StatusBadge>
				{/if}
				<span class="text-muted-foreground tabular-nums">
					{formatDuration(attempt.duration_ms)}{#if attempt.started_at}{' · '}{clockLabel(
							attempt.started_at
						)}{/if}
				</span>
				{#if hasBody}
					{#if callOpen}<ChevronDownIcon
							class="text-muted-foreground size-3.5"
						/>{:else}<ChevronRightIcon class="text-muted-foreground size-3.5" />{/if}
				{/if}
			</span>
		</button>
		{#if attempt.method && attempt.url && attempt.error && attempt.error !== node.state.message}
			<p class="text-warning-strong px-3 pb-2 text-xs">{attempt.error}</p>
		{/if}
		{#if callOpen && hasBody}
			{@const call = isCall(attempt)}
			<div
				class="border-border/70 @min-[40rem]:grid-cols-2 @min-[40rem]:divide-x @min-[40rem]:divide-border/70 grid border-t"
			>
				<div class="min-w-0 px-3 py-2">
					<div class="mb-1 flex items-center justify-between">
						<p class="text-muted-foreground text-xs font-medium">{call ? 'Request' : 'Input'}</p>
						{#if request}<CopyButton
								value={raw(attempt.request)}
								label={call ? 'Copy request' : 'Copy input'}
								size="sm"
							/>{/if}
					</div>
					{#if request}{@render jsonBlock(request)}{:else}
						<p class="text-muted-foreground text-xs">{call ? 'No request body.' : 'No input.'}</p>
					{/if}
				</div>
				<div class="border-border/70 @min-[40rem]:border-t-0 min-w-0 border-t px-3 py-2">
					<div class="mb-1 flex items-center justify-between">
						<p class="text-muted-foreground text-xs font-medium">
							{call ? 'Response' : 'Output'}{#if attempt.recovered}<span class="font-normal">
									· read from the failure message</span
								>{/if}
						</p>
						{#if response}<CopyButton
								value={raw(attempt.response)}
								label={call ? 'Copy response' : 'Copy output'}
								size="sm"
							/>{/if}
					</div>
					{#if response}{@render jsonBlock(response)}{:else}
						<p class="text-muted-foreground text-xs">
							{call
								? attempt.outcome === 'ok'
									? 'No response body.'
									: 'No response recorded.'
								: 'No output.'}
						</p>
					{/if}
				</div>
			</div>
		{/if}
	</div>
{/snippet}

{#if onlyProblems && !hasProblemBelow && !live}
	<!-- Folded away under "only problems": a healthy node is one muted line, so the count still adds up. -->
	<div class="text-muted-foreground flex min-h-9 items-center gap-2 px-4 text-xs">
		<span class="size-1.5 rounded-full {dotClass}"></span>
		<span>{node.name}</span>
		<span class="ml-auto">{runStatusLabel(node.status)}</span>
	</div>
{:else}
	<div class="@container" data-run-node={node.uid}>
		<div class="flex min-h-11 items-center gap-3 px-4 py-2.5 {isComposite ? 'bg-muted/30' : ''}">
			<span class="size-2 shrink-0 rounded-full {dotClass}" aria-hidden="true"></span>
			<div class="flex min-w-0 flex-1 flex-col gap-0.5">
				<div class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm">
					<span class="font-medium">{node.name}</span>
					{#if !isGate}
						<span class="text-muted-foreground font-mono text-[11px]">{node.action_key}</span>
					{/if}
					{#if node.case_no != null}
						<span class="text-warning-strong font-mono text-[11px] font-semibold"
							>RV-{node.case_no}</span
						>
					{/if}
				</div>
				{#if meta}
					<p class="text-muted-foreground text-xs">{meta}</p>
				{/if}
				{#if node.failure?.headline && !isComposite}
					<p class="text-warning-strong text-xs font-medium">{node.failure.headline}</p>
				{:else if problem && node.state.message && isComposite}
					<p class="text-muted-foreground text-xs">{node.state.message}</p>
				{/if}
			</div>
			{#if !isGate}
				<span
					class="text-muted-foreground border-border/70 hidden rounded border px-1.5 text-[10.5px] font-semibold tracking-wide @min-[36rem]:inline-flex"
					>{providerLabel(node.provider)}</span
				>
			{/if}
			{#if summary?.kind === 'call' && !open}
				<span
					class="text-muted-foreground hidden min-w-0 max-w-60 items-center gap-1.5 truncate font-mono text-[11px] @min-[48rem]:inline-flex"
				>
					<span class="text-foreground font-medium">{summary.attempt.method}</span>
					<span class="truncate" title={summary.attempt.url ?? ''}
						>{pathOf(summary.attempt.url)}</span
					>
				</span>
				{#if summary.attempt.status_code != null}
					<StatusBadge tone={httpTone(summary.attempt.status_code)}
						>{summary.attempt.status_code}</StatusBadge
					>
				{/if}
			{:else if summary?.kind === 'result' && !open}
				<span
					class="text-muted-foreground hidden min-w-0 max-w-72 truncate text-xs @min-[48rem]:inline"
					title={summary.text}>{summary.text}</span
				>
			{/if}
			<span class="text-muted-foreground w-14 shrink-0 text-right text-xs tabular-nums"
				>{duration}</span
			>
			<StatusBadge tone={runStatusTone(node.status)} pulse={node.state.type === 'running'}>
				{runStatusLabel(node.status)}
			</StatusBadge>
			{#if canRetryHere && enhance}
				<form method="POST" action={retryAction} use:enhance={after('Queued again')}>
					<input type="hidden" name="step_id" value={node.uid} />
					<Button type="submit" size="sm" variant="outline">
						<RotateCcwIcon class="size-3.5" />Retry
					</Button>
				</form>
			{:else if isComposite || node.attempts.length > 0 || node.result != null || isGate}
				<button
					type="button"
					class="text-muted-foreground hover:text-foreground inline-flex size-7 items-center justify-center rounded"
					aria-expanded={open}
					aria-label={open ? `Collapse ${node.name}` : `Expand ${node.name}`}
					onclick={() => (manual = !open)}
				>
					{#if open}<ChevronDownIcon class="size-4" />{:else}<ChevronRightIcon
							class="size-4"
						/>{/if}
				</button>
			{/if}
		</div>

		{#if open}
			{#if isGate}
				<div class="ml-9 mr-4 mb-3 flex flex-col gap-2 text-xs">
					{#if node.justification}
						<p class="bg-muted/40 rounded-md px-3 py-2">“{node.justification}”</p>
					{/if}
					{#if node.decision}
						<p class="text-muted-foreground">
							{node.decision === 'approved' ? 'Approved' : 'Rejected'} by
							<span class="text-foreground font-medium">{node.decided_by_name ?? 'someone'}</span>
							{#if node.decided_at}· {clockLabel(node.decided_at)}{/if}
							{#if node.decision_note}<span class="block">“{node.decision_note}”</span>{/if}
						</p>
					{:else if canDecideHere && enhance && decideAction}
						<div class="flex items-center gap-2">
							<DecideDialogs
								runUid={parentUid ?? node.uid}
								{isReview}
								action={decideAction}
								{enhance}
								size="sm"
							/>
							<span class="text-muted-foreground">You can decide this one.</span>
						</div>
					{:else if node.state.type === 'paused'}
						<p class="text-muted-foreground">
							{node.approver_roles.length
								? `Only ${node.approver_roles.join(' or ')} can decide this.`
								: 'Waiting for a second person to decide.'}
						</p>
					{/if}
				</div>
			{:else if isComposite}
				<div class="border-border/60 divide-border/60 ml-7 mr-0 flex flex-col divide-y border-l">
					{#each node.children as child (child.uid)}
						<RunNode
							node={child}
							{canManage}
							{enhance}
							{retryAction}
							{decideAction}
							{decidable}
							{onlyProblems}
							{expandAll}
							{links}
							{now}
							parentUid={node.uid}
							{isReview}
						/>
					{/each}
				</div>
			{:else}
				<div class="ml-9 mr-4 mb-3 flex flex-col gap-2">
					{#if node.attempts.length}
						<div
							class="overflow-hidden rounded-md border {problem
								? 'border-warning-line'
								: 'border-border/70'}"
						>
							{#each node.attempts as attempt (attempt.attempt_no)}
								{@render attemptRow(attempt)}
							{/each}
						</div>
					{/if}
					{#if node.result != null && lines(node.result) && !node.attempts.some((a) => a.url)}
						<div class="bg-muted/40 rounded-md px-3 py-2">
							<div class="mb-1 flex items-center justify-between">
								<p class="text-muted-foreground text-xs font-medium">Result</p>
								<CopyButton value={raw(node.result)} label="Copy result" size="sm" />
							</div>
							{@render jsonBlock(lines(node.result) ?? [])}
						</div>
					{/if}
					{#if node.attempts.length === 0 && node.result == null && node.state.name !== 'Skipped'}
						<p class="text-muted-foreground text-xs">Not run yet.</p>
					{/if}
				</div>
			{/if}
		{/if}
	</div>
{/if}
