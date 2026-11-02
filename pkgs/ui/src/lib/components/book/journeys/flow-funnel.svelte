<script lang="ts">
	import type { Component } from 'svelte';
	// The step funnel for one published journey version — where it actually loses people.
	//
	// A shared book surface, not a platform page: the platform mounts it at
	// /flows/[id]/insights and the admin portal will mount the same component read-only over its
	// own `?tenant=`-scoped loader. So no hrefs are hardcoded (they come from `links`), the version
	// and range pickers are plain GET links the app builds, and there is no mutation anywhere on
	// this surface at all — it is a report.
	//
	// Every number comes from the server (`GET /flows/{uid}/insights`). Nothing here recomputes a
	// rate the API already produced; the helpers in `insights.ts` are presentation only.
	import ActivityIcon from '@lucide/svelte/icons/activity';
	import GitBranchIcon from '@lucide/svelte/icons/git-branch';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import ZapIcon from '@lucide/svelte/icons/zap';
	import CircleDotIcon from '@lucide/svelte/icons/circle-dot';
	import EarIcon from '@lucide/svelte/icons/ear';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import TrendingDownIcon from '@lucide/svelte/icons/trending-down';
	import { Button } from '../../ui/button/index.js';
	import { Card } from '../../ui/card/index.js';
	import EmptyState from '../../ui/empty-state/empty-state.svelte';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import StatTile from '../../data/stat-tile.svelte';
	import PageHeader from '../../shell/page-header.svelte';
	import { NODE_KIND_LABEL } from './nodes.js';
	import {
		FUNNEL_STATUSES,
		STATUS_LABEL,
		STATUS_TONE,
		barWidth,
		completionRate,
		durationLabel,
		edgesByTarget,
		lostInto,
		outstanding,
		percent,
		versionLabel,
		worstNode,
		RANGE_PRESETS,
		type FlowInsights,
		type FunnelNode,
		type FunnelStepStatus,
		type FlowFunnelLinks
	} from './insights.js';

	let {
		insights,
		links,
		loadFailed = false,
		/** The window currently applied, in days — so the preset chips can show which is active. */
		days = 90,
		onretry
	}: {
		insights: FlowInsights | null;
		links: FlowFunnelLinks;
		loadFailed?: boolean;
		days?: number;
		onretry?: () => void;
	} = $props();

	const nodes = $derived(insights?.nodes ?? []);
	const byTarget = $derived(edgesByTarget(insights?.edges ?? []));
	const worst = $derived(worstNode(nodes));
	const completedCases = $derived(insights?.cases.by_status.completed ?? 0);
	const totalCases = $derived(insights?.cases.total ?? 0);

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

	function statuses(node: FunnelNode): { status: FunnelStepStatus; count: number }[] {
		return FUNNEL_STATUSES.map((status) => ({ status, count: node.by_status[status] ?? 0 })).filter(
			(entry) => entry.count > 0
		);
	}

	// `noUncheckedIndexedAccess` types the last element as possibly undefined, and the fallback
	// must be a real preset rather than a `!`: the widen action is a LINK, so an undefined day
	// count would render an href the page cannot answer.
	const widestRange = RANGE_PRESETS[RANGE_PRESETS.length - 1] ?? { days: 365, label: '12 months' };
	const windowLabel = $derived(
		RANGE_PRESETS.find((preset) => preset.days === days)?.label ?? `${days} days`
	);
</script>

{#snippet widenWindow()}
	<Button
		variant="outline"
		size="sm"
		href={links.insights({ version: insights?.version.uid, days: widestRange.days })}
		>Look back {widestRange.label}</Button
	>
{/snippet}

<div class="space-y-6">
	<PageHeader
		eyebrow="Journeys"
		title={insights ? insights.flow.name : 'Journey insights'}
		description="Where this journey gets to, and where it stops."
		back={{ href: links.flow, label: 'Back to the journey' }}
	/>

	{#if loadFailed}
		<LoadErrorBanner message="We couldn’t load this journey’s insights." onRetry={onretry} />
	{:else if !insights}
		<EmptyState
			icon={ActivityIcon}
			title="Nothing to report yet"
			description="Publish a version of this journey and the funnel appears once clients start
				going through it."
		/>
	{:else}
		<!-- Cohort controls. Plain links, not a form: the version and the window are the page's
		     identity, so they belong in the URL where a colleague can be sent one. -->
		<div class="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
			<div class="flex flex-wrap items-center gap-2">
				<span class="text-muted-foreground text-xs tracking-wide uppercase">Version</span>
				{#each insights.versions as version (version.uid)}
					<a
						href={links.insights({ version: version.uid, days })}
						class="rounded-md border px-2 py-1 text-xs {version.uid === insights.version.uid
							? 'border-primary/30 bg-primary/10 text-primary'
							: 'border-border text-muted-foreground hover:text-foreground'}"
					>
						{versionLabel(version)}
					</a>
				{/each}
			</div>
			<div class="flex flex-wrap items-center gap-2">
				<span class="text-muted-foreground text-xs tracking-wide uppercase"
					>Started in the last</span
				>
				{#each RANGE_PRESETS as preset (preset.days)}
					<a
						href={links.insights({ version: insights.version.uid, days: preset.days })}
						class="rounded-md border px-2 py-1 text-xs {preset.days === days
							? 'border-primary/30 bg-primary/10 text-primary'
							: 'border-border text-muted-foreground hover:text-foreground'}"
					>
						{preset.label}
					</a>
				{/each}
			</div>
		</div>

		{#if totalCases === 0}
			<!-- This read "No journeys started on this version yet" while the page was showing a
			     WINDOW — so a version last used four months ago reported, under a 30-day cohort, that
			     it had never run. The window is half the question the reader asked and the state now
			     says which one it answered; the widest preset is offered as the way out, since
			     widening is the one thing that can turn this answer around, and it is withheld when
			     the reader is already on it (a control that changes nothing is worse than none). -->
			<EmptyState
				icon={ActivityIcon}
				title="No journeys started in this window"
				description="Nothing went through {insights.flow.name} on v{insights.version
					.version} in the last {windowLabel}. Journeys started earlier are outside the cohort."
				action={days === widestRange.days ? undefined : widenWindow}
			/>
		{:else}
			<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
				<StatTile label="Journeys started" value={String(totalCases)} />
				<StatTile
					label="Completed"
					value={percent(totalCases ? completedCases / totalCases : null)}
					sub={`${completedCases} of ${totalCases}`}
					tone={completedCases === totalCases ? 'positive' : 'neutral'}
				/>
				<StatTile
					label="Typical time to complete"
					value={durationLabel(insights.cases.median_seconds) ?? '—'}
					sub="median, from start to finish"
				/>
				<StatTile
					label="Biggest hold-up"
					value={worst ? String(outstanding(worst)) : '—'}
					sub={worst ? `stuck at ${worst.title}` : 'nothing is stuck'}
					tone={worst ? 'negative' : 'positive'}
					icon={TrendingDownIcon}
				/>
			</div>

			{#if insights.shape === 'legacy'}
				<p class="text-muted-foreground text-xs">
					This version predates the graph builder, so its steps have no authored order — the counts
					below are per step, without drop-off between them.
				</p>
			{:else if insights.shape === 'unreadable'}
				<p class="text-warning-strong text-xs">
					This version’s definition can’t be read by the current engine, so only the counts are
					shown.
				</p>
			{/if}

			<Card class="divide-border divide-y p-0">
				{#each nodes as node (node.node_id)}
					{@const Icon = KIND_ICON[node.kind] ?? CircleDotIcon}
					{@const lost = lostInto(node.node_id, byTarget)}
					{@const stuck = outstanding(node)}
					<div class="space-y-2 p-4">
						{#if lost > 0}
							<p class="text-warning-strong flex items-center gap-1 text-xs">
								<TrendingDownIcon class="size-3.5" />
								{lost} didn’t get this far
							</p>
						{/if}
						<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
							<Icon class="text-muted-foreground size-4 shrink-0 self-center" />
							<span class="text-xs font-medium">{node.title || node.node_id}</span>
							{#if !node.is_work}
								<StatusBadge tone="neutral">{NODE_KIND_LABEL[node.kind]}</StatusBadge>
							{/if}
							<span class="text-muted-foreground text-xs">
								{node.reached}
								{node.reached === 1 ? 'journey' : 'journeys'}
							</span>
							{#if node.is_work}
								<span class="text-muted-foreground text-xs">
									{percent(completionRate(node))} completed
								</span>
							{/if}
							{#if durationLabel(node.median_seconds)}
								<span class="text-muted-foreground text-xs">
									typically {durationLabel(node.median_seconds)}
									{#if durationLabel(node.p90_seconds)}
										· slowest 10% over {durationLabel(node.p90_seconds)}
									{/if}
								</span>
							{/if}
							{#if stuck > 0}
								<StatusBadge tone="warning">{stuck} outstanding</StatusBadge>
							{/if}
						</div>

						<!-- Volume relative to the busiest node, so the eye compares nodes rather than
						     each node against its own 100%. -->
						<div class="bg-muted h-1.5 w-full overflow-hidden rounded-full">
							<div
								class="bg-primary h-full rounded-full"
								style={`width: ${barWidth(node, nodes)}%`}
							></div>
						</div>

						{#if statuses(node).length}
							<div class="flex flex-wrap gap-1.5">
								{#each statuses(node) as entry (entry.status)}
									<StatusBadge tone={STATUS_TONE[entry.status]}>
										{STATUS_LABEL[entry.status]}
										{entry.count}
									</StatusBadge>
								{/each}
							</div>
						{/if}

						{#if node.arms?.length}
							<ul class="text-muted-foreground space-y-0.5 text-xs">
								{#each node.arms as arm (arm.id)}
									<li>
										<span class="text-foreground">{arm.label || arm.id}</span>
										{#if arm.is_else}(otherwise){/if}
										— {arm.count}
										{arm.count === 1 ? 'journey' : 'journeys'}
										{#if node.reached}
											({percent(arm.count / node.reached)})
										{/if}
									</li>
								{/each}
							</ul>
						{/if}
					</div>
				{/each}
			</Card>
		{/if}
	{/if}
</div>
