<script lang="ts">
	import {
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		EmptyState,
		PageHeader,
		REFRESH_WHILE,
		RefreshWhile,
		StatusBadge,
		type StatusTone
	} from '@graphene/ui';
	import ShieldIcon from '@lucide/svelte/icons/shield-check';
	import { invalidateAll } from '$app/navigation';
	import {
		KIND_LABEL,
		amlPill,
		clientHref,
		identityPill,
		riskOf,
		rowAwaitingResult,
		shortDate
	} from '$lib/aml';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const FIRM_STATUS: Record<string, { label: string; tone: StatusTone }> = {
		not_started: { label: 'Not started', tone: 'neutral' },
		pending: { label: 'In progress', tone: 'info' },
		passed: { label: 'Passed', tone: 'success' },
		referred: { label: 'Referred', tone: 'warning' },
		failed: { label: 'Failed', tone: 'danger' }
	};

	const row = $derived(data.graphene?.client ?? null);
	const screenStarting = $derived(
		row?.aml?.status === 'awaiting_screen' && row.aml.queued === true
	);
	const waiting = $derived(
		screenStarting ||
			data.screening.checks.some((item) => item.status === 'pending') ||
			(row != null && rowAwaitingResult(row))
	);
</script>

<RefreshWhile
	active={waiting}
	interval={screenStarting ? REFRESH_WHILE.screenStarting : REFRESH_WHILE.pendingCheck}
	onpoll={() => invalidateAll()}
/>

<svelte:head><title>Compliance · {data.clientName}</title></svelte:head>
<div class="space-y-5">
	<PageHeader
		title="Compliance"
		description="What the firm sees, and the checks behind it in Graphene's AML console."
	/>

	<Card glass class="gap-2">
		<CardHeader><CardTitle>Firm view</CardTitle></CardHeader>
		<CardContent>
			{#if data.screening.checks.length === 0}
				<p class="text-muted-foreground text-sm">No checks yet.</p>
			{:else}
				<ul class="divide-border divide-y text-sm">
					{#each data.screening.checks as item (item.kind)}
						{@const s = FIRM_STATUS[item.status] ?? { label: item.status, tone: 'neutral' }}
						<li class="flex items-center justify-between gap-3 py-2">
							<span>{KIND_LABEL[item.kind]}</span>
							<span class="flex items-center gap-3">
								<span class="text-muted-foreground tabular-nums">{shortDate(item.updated_at)}</span>
								<StatusBadge tone={s.tone}>{s.label}</StatusBadge>
							</span>
						</li>
					{/each}
				</ul>
			{/if}
		</CardContent>
	</Card>

	{#if data.canReadAml}
		<Card glass class="gap-2">
			<CardHeader class="flex flex-row flex-wrap items-center justify-between gap-2">
				<CardTitle>Graphene's view</CardTitle>
				{#if row}
					<Button variant="outline" size="sm" href={clientHref(row.key)}>Open in the console</Button
					>
				{/if}
			</CardHeader>
			<CardContent>
				{#if data.graphene === null}
					<p class="text-muted-foreground text-sm">The checks could not be loaded.</p>
				{:else if row === null}
					<EmptyState
						icon={ShieldIcon}
						title="Not screened yet"
						description="Graphene has not run a check for this client."
					/>
				{:else}
					{@const identity = identityPill(row.identity)}
					{@const aml = amlPill(row.aml, row.origin)}
					{@const risk = riskOf(row.risk)}
					<ul class="divide-border divide-y text-sm">
						<li class="flex items-center justify-between gap-3 py-2">
							<a href={clientHref(row.key)} class="font-medium hover:underline"
								>{KIND_LABEL.identity}</a
							>
							<span class="flex items-center gap-3">
								<span class="text-muted-foreground tabular-nums"
									>{shortDate(row.identity?.updated_at ?? null)}</span
								>
								<StatusBadge tone={identity.tone}>{identity.label}</StatusBadge>
							</span>
						</li>
						<li class="flex items-center justify-between gap-3 py-2">
							<a href={clientHref(row.key)} class="font-medium hover:underline">{KIND_LABEL.aml}</a>
							<span class="flex items-center gap-3">
								<span class="text-muted-foreground tabular-nums"
									>{shortDate(row.aml?.updated_at ?? null)}</span
								>
								{#if row.stale}<StatusBadge tone="warning">Stale</StatusBadge>{/if}
								<StatusBadge tone={aml.tone}>{aml.label}</StatusBadge>
							</span>
						</li>
						<li class="flex items-center justify-between gap-3 py-2">
							<span class="font-medium">Risk</span>
							<StatusBadge tone={risk.tone}>{risk.label}</StatusBadge>
						</li>
					</ul>
					{#if row.needs_attention}
						<p class="mt-2">
							<StatusBadge tone="warning">Needs attention</StatusBadge>
						</p>
					{/if}
				{/if}
			</CardContent>
		</Card>
	{/if}
</div>
