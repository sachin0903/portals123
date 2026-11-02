<script lang="ts">
	import type { Component } from 'svelte';
	// The flow dry run — pick a subject, see what the journey WOULD do to them. Nothing happens.
	//
	// The rule tier's `DryRunPanel` (@graphene/ui/automations) does this for one rule; this is the
	// same idea one level up, and it deliberately reads the same way: the verdict comes from the
	// SERVER, each condition shows the value it actually read, and the panel states plainly that
	// nothing ran. An author who has used one has used both.
	//
	// Everything mutating is injected. `actionName` + `enhance` come from the host page (pkgs/ui
	// cannot import `$app/forms`), and absent ⇒ the panel explains why the dry run isn't offered
	// rather than rendering a dead button — the read-only posture the admin portal mounts under.
	import FlaskConicalIcon from '@lucide/svelte/icons/flask-conical';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import GitBranchIcon from '@lucide/svelte/icons/git-branch';
	import ZapIcon from '@lucide/svelte/icons/zap';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import CircleDotIcon from '@lucide/svelte/icons/circle-dot';
	import EarIcon from '@lucide/svelte/icons/ear';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import CheckIcon from '@lucide/svelte/icons/check';
	import XIcon from '@lucide/svelte/icons/x';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import { Alert, AlertDescription, AlertTitle } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { Separator } from '../../ui/separator/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import SelectMenu from '../../ui/select-menu/select-menu.svelte';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import ExplainTree from '../../automations/explain-tree.svelte';
	import { NODE_KIND_LABEL } from './nodes.js';
	import { formatDate } from '../../../dates.js';
	import {
		actionSummary,
		exclusionLabel,
		fanOutLabel,
		outcomeHeadline,
		outcomeIsHealthy,
		splitExclusions,
		tierLabel,
		totalDurationLabel,
		whenLabel,
		type DryRunClient,
		type FlowSimulation,
		type SimHop
	} from './simulation.js';
	import type { Enhance } from '../clients/form-enhance.js';

	let {
		result = null,
		clients = [],
		actionName,
		enhance,
		pending = false,
		error = null
	}: {
		/** The last dry run's outcome, from the host page's form action. */
		result?: FlowSimulation | null;
		/** The clients the subject picker offers. Empty ⇒ described-subject only. */
		clients?: DryRunClient[];
		/** The host page's dry-run action name. Absent ⇒ the panel explains why it isn't offered. */
		actionName?: string;
		enhance?: Enhance;
		pending?: boolean;
		error?: string | null;
	} = $props();

	let mode = $state<'real' | 'synthetic'>('real');
	let contactId = $state('');
	let contactType = $state('individual');
	let custody = $state('onshore');
	let products = $state('');

	const clientItems = $derived(
		clients.map((client) => ({ value: String(client.id), label: client.name }))
	);
	const exclusions = $derived(result ? splitExclusions(result) : null);
	const duration = $derived(result ? totalDurationLabel(result) : null);

	// Typed by string, not by FlowNodeKind: the kind vocabulary is read off flow-contract.json, so
	// TypeScript widens it to `string` and a `Record<FlowNodeKind, …>` would not be indexable by it.
	const KIND_ICON: Record<string, Component<{ class?: string }>> = {
		step: CircleDotIcon,
		action: ZapIcon,
		branch: GitBranchIcon,
		wait: ClockIcon,
		wait_for_event: EarIcon,
		start_flow: ArrowRightIcon,
		exit: LogOutIcon
	};

	function hopSubtitle(hop: SimHop): string {
		if (hop.kind === 'action') return actionSummary(hop);
		if (hop.kind === 'step') {
			const parts = [tierLabel(hop), fanOutLabel(hop)].filter(Boolean);
			return parts.length ? parts.join(' · ') : (hop.type ?? '').replace(/_/g, ' ');
		}
		if (hop.kind === 'wait' && hop.resumes_at) {
			return `resumes ${formatDate(hop.resumes_at)}`;
		}
		if (hop.kind === 'wait_for_event' && hop.listens_for) {
			return `listening for ${hop.listens_for.replace(/[._]/g, ' ')}`;
		}
		return NODE_KIND_LABEL[hop.kind] ?? hop.kind;
	}
</script>

<div class="space-y-5">
	{#if actionName && enhance}
		<form method="POST" action={`?/${actionName}`} use:enhance class="space-y-3">
			<input type="hidden" name="kind" value={mode} />
			<div class="flex flex-wrap items-end gap-2">
				<div class="space-y-1">
					<Label for="dry-run-mode">Test against</Label>
					<SelectMenu
						id="dry-run-mode"
						class="w-56"
						items={[
							{ value: 'real', label: 'A client from the book' },
							{ value: 'synthetic', label: 'A client I’ll describe' }
						]}
						bind:value={mode}
					/>
				</div>

				{#if mode === 'real'}
					<div class="space-y-1">
						<Label for="dry-run-client">Client</Label>
						{#if clientItems.length}
							<SelectMenu
								id="dry-run-client"
								name="contact_id"
								class="w-64"
								placeholder="Choose a client…"
								items={clientItems}
								bind:value={contactId}
							/>
						{:else}
							<Input
								id="dry-run-client"
								name="contact_id"
								type="number"
								min="1"
								placeholder="Client record id"
								class="w-40"
								bind:value={contactId}
							/>
						{/if}
					</div>
				{:else}
					<div class="space-y-1">
						<Label for="dry-run-type">Client type</Label>
						<SelectMenu
							id="dry-run-type"
							name="contact_type"
							class="w-44"
							items={[
								{ value: 'individual', label: 'Individual' },
								{ value: 'organisation', label: 'Organisation' },
								{ value: 'child', label: 'Child' }
							]}
							bind:value={contactType}
						/>
					</div>
					<div class="space-y-1">
						<Label for="dry-run-custody">Custody</Label>
						<SelectMenu
							id="dry-run-custody"
							name="custody"
							class="w-36"
							items={[
								{ value: 'onshore', label: 'Onshore' },
								{ value: 'offshore', label: 'Offshore' }
							]}
							bind:value={custody}
						/>
					</div>
					<div class="space-y-1">
						<Label for="dry-run-products">Products</Label>
						<Input
							id="dry-run-products"
							name="products"
							placeholder="ISA, SIPP"
							class="w-44"
							bind:value={products}
						/>
					</div>
				{/if}

				<Button type="submit" variant="outline" disabled={pending}>
					<FlaskConicalIcon class="size-4" />
					{pending ? 'Working…' : 'Dry run'}
				</Button>
			</div>
			<p class="text-muted-foreground text-xs">
				A dry run never starts a journey, never plans a step, never raises a task and never sends
				anything. It only reports what would happen.
			</p>
		</form>
	{:else}
		<p class="text-muted-foreground text-sm">You don’t have permission to dry-run this journey.</p>
	{/if}

	{#if error}
		<Alert variant="destructive">
			<AlertTitle>That didn’t run</AlertTitle>
			<AlertDescription>{error}</AlertDescription>
		</Alert>
	{/if}

	{#if result}
		<Separator />

		<Alert>
			<AlertTitle class="flex items-center gap-2">
				{#if !outcomeIsHealthy(result.outcome.kind)}
					<TriangleAlertIcon class="text-warning-strong size-4" />
				{/if}
				{outcomeHeadline(result)}
			</AlertTitle>
			<AlertDescription>
				<p>
					Nothing was performed — this was a dry run against
					<span class="font-medium">{result.subject.label}</span>.
					{#if duration}
						Start to finish it would take {duration}.
					{/if}
				</p>
				{#if result.outcome.detail}
					<p class="mt-1">{result.outcome.detail}</p>
				{/if}
				{#if result.shape === 'legacy'}
					<p class="mt-1">
						This journey predates the graph builder, so its steps run when their prerequisites
						settle rather than along a path.
					</p>
				{/if}
			</AlertDescription>
		</Alert>

		{#if result.issues.length}
			<Alert variant="destructive">
				<AlertTitle>This journey can’t be published yet</AlertTitle>
				<AlertDescription>
					<ul class="mt-1 list-disc space-y-0.5 pl-5 text-sm">
						{#each result.issues as issue (issue)}
							<li>{issue}</li>
						{/each}
					</ul>
				</AlertDescription>
			</Alert>
		{/if}

		<div class="text-muted-foreground flex flex-wrap gap-x-6 gap-y-1 text-xs">
			<span>{result.tiers.cases} {result.tiers.cases === 1 ? 'case' : 'cases'}</span>
			<span>{result.tiers.step_instances} steps planned</span>
			<span>{result.tiers.participants} in the journey</span>
			<span>{result.tiers.accounts} {result.tiers.accounts === 1 ? 'account' : 'accounts'}</span>
		</div>

		<!-- The path -->
		<div class="space-y-1">
			<p class="text-muted-foreground text-xs font-medium tracking-wide uppercase">
				{result.shape === 'graph' ? 'The path' : 'What applies'}
			</p>
			{#if !result.path.length}
				<EmptyState
					compact
					icon={FlaskConicalIcon}
					title="Nothing happens for this client"
					description="Every step in this journey is conditioned out for the subject you picked."
				/>
			{:else}
				<ol class="space-y-2">
					{#each result.path as hop, index (hop.node_id)}
						{@const Icon = KIND_ICON[hop.kind] ?? CircleDotIcon}
						<li class="border-border flex gap-3 rounded-lg border p-3">
							<div class="flex flex-col items-center gap-1 pt-0.5">
								<Icon class="text-muted-foreground size-4 shrink-0" />
								{#if index < result.path.length - 1}
									<span class="bg-border w-px flex-1"></span>
								{/if}
							</div>
							<div class="min-w-0 flex-1 space-y-1">
								<div class="flex flex-wrap items-center gap-2">
									<span class="text-xs font-medium">{hop.title || hop.node_id}</span>
									<StatusBadge tone="neutral">{NODE_KIND_LABEL[hop.kind]}</StatusBadge>
									{#if hop.blocking}
										<StatusBadge tone="warning">waits here</StatusBadge>
									{/if}
									{#if hop.at_day > 0}
										<span class="text-muted-foreground text-xs">{whenLabel(hop.at_day)}</span>
									{/if}
								</div>
								<p class="text-muted-foreground text-xs">{hopSubtitle(hop)}</p>

								{#if hop.note}
									<p class="text-warning-strong text-xs">{hop.note}</p>
								{/if}

								{#if hop.visibility}
									<details class="text-xs">
										<summary class="text-muted-foreground cursor-pointer">
											{hop.instances.length ? 'Why it applies' : 'Why it doesn’t apply'}
										</summary>
										<div class="mt-1"><ExplainTree node={hop.visibility} /></div>
									</details>
								{/if}

								{#if hop.arms.length}
									<ul class="mt-1 space-y-1">
										{#each hop.arms as arm (arm.id)}
											<li
												class="rounded-md border p-2 text-xs {arm.taken
													? 'border-success-line bg-success-subtle'
													: 'border-border'}"
											>
												<div class="flex items-center gap-2">
													{#if arm.taken}
														<CheckIcon class="text-success-strong size-3.5 shrink-0" />
													{:else if arm.reached}
														<XIcon class="text-muted-foreground size-3.5 shrink-0" />
													{/if}
													<span class="font-medium">{arm.label || arm.id}</span>
													{#if arm.is_else}
														<StatusBadge tone="neutral">otherwise</StatusBadge>
													{/if}
													{#if !arm.reached && !arm.is_else}
														<span class="text-muted-foreground">never evaluated</span>
													{/if}
													{#if arm.next}
														<span class="text-muted-foreground">→ {arm.next}</span>
													{/if}
												</div>
												{#if arm.explanation}
													<div class="mt-1"><ExplainTree node={arm.explanation} /></div>
												{/if}
											</li>
										{/each}
									</ul>
									{#if hop.elided.length}
										<p class="text-muted-foreground text-xs">
											Skipped by this decision: {hop.elided.join(', ')}
										</p>
									{/if}
								{/if}

								{#if hop.timeout_branch}
									<p class="text-muted-foreground text-xs">
										If the deadline passes it goes to <span class="font-medium"
											>{hop.timeout_branch}</span
										>.
									</p>
								{/if}
							</div>
						</li>
					{/each}
				</ol>
			{/if}
		</div>

		<!-- What this client never sees -->
		{#if exclusions && (exclusions.byDesign.length || exclusions.problems.length)}
			<div class="space-y-2">
				<p class="text-muted-foreground text-xs font-medium tracking-wide uppercase">
					Not on this path
				</p>
				{#if exclusions.problems.length}
					<Alert variant="destructive">
						<AlertTitle>Some steps can’t be reached at all</AlertTitle>
						<AlertDescription>
							<ul class="mt-1 space-y-0.5 text-sm">
								{#each exclusions.problems as item (item.node_id)}
									<li>{item.title || item.node_id} — {exclusionLabel(item.reason)}</li>
								{/each}
							</ul>
						</AlertDescription>
					</Alert>
				{/if}
				{#if exclusions.byDesign.length}
					<ul class="text-muted-foreground space-y-0.5 text-xs">
						{#each exclusions.byDesign as item (item.node_id)}
							<li>
								<span class="text-foreground">{item.title || item.node_id}</span>
								— {exclusionLabel(item.reason)}{item.because_of ? ` (${item.because_of})` : ''}
							</li>
						{/each}
					</ul>
				{/if}
			</div>
		{/if}
	{/if}
</div>
