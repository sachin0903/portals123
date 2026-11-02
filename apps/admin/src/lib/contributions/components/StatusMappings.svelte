<script lang="ts">
	import { Alert, AlertDescription, Badge, Table } from '@graphene/ui';
	import { STATUSES } from '$lib/contributions/status';
	import type { ContributionStatus } from '$lib/contributions/types';
	import StatusBadge from './StatusBadge.svelte';

	// 1 — workbench ↔ Morgan Lloyd contribution status
	const mlMap: {
		status: ContributionStatus;
		meaning: string;
		ml: string;
		tbc?: boolean;
		by: string;
	}[] = [
		{
			status: 'identified',
			meaning: 'Credit seen in the SEI daily feed; not yet sent to ML',
			ml: 'Not in Morgan Lloyd',
			by: 'SEI transaction feed'
		},
		{
			status: 'awaiting_confirmation',
			meaning: 'Ready for operations to classify',
			ml: 'Not in Morgan Lloyd',
			by: 'Operations'
		},
		{
			status: 'confirmed',
			meaning: 'Classified and eligibility checked',
			ml: 'Not in Morgan Lloyd',
			by: 'Operations'
		},
		{
			status: 'ml_recorded',
			meaning: 'Contribution created on the ML scheme',
			ml: 'Received',
			by: 'Morgan Lloyd'
		},
		{
			status: 'ras_applied',
			meaning: 'ML applied to HMRC for basic-rate relief',
			ml: 'RAS Applied',
			tbc: true,
			by: 'Morgan Lloyd'
		},
		{
			status: 'ras_received',
			meaning: 'ML has received the relief from HMRC',
			ml: 'RAS Received',
			tbc: true,
			by: 'Morgan Lloyd'
		},
		{
			status: 'tax_relief_received',
			meaning: 'Relief posted to the member at SEI',
			ml: 'Paid to SEI',
			tbc: true,
			by: 'SEI cash receipt'
		},
		{
			status: 'reconciled',
			meaning: 'Matched across Graphene, SEI and Morgan Lloyd',
			ml: 'Complete',
			tbc: true,
			by: 'Daily reconciliation'
		},
		{
			status: 'manual_intervention',
			meaning: 'Requires manual review',
			ml: '—',
			by: 'Operations'
		}
	];

	const activityStatus = [
		[1, 'Waiting for Parent Dependency'],
		[2, 'Waiting for Dependency'],
		[3, 'Scheduled'],
		[4, 'Cancelled'],
		[5, 'Complete'],
		[6, 'Ready'],
		[7, 'In Process'],
		[9, 'Exception'],
		[10, 'Authorization'],
		[11, 'Manual Step'],
		[12, 'On Hold'],
		[13, 'Reversed'],
		[14, 'Review'],
		[16, 'Partially Reversed'],
		[17, 'Partially Complete']
	] as const;
	const requestStatus = [
		[1, 'Requested'],
		[2, 'Created'],
		[3, 'Validated (direct posting → no pending-approval state)']
	] as const;
	const flags = [
		['Reversal', 'Transaction reverses an earlier entry'],
		['Adjustment', 'Transaction adjusts an earlier entry'],
		['Suppressed', 'Transaction is hidden from statements and notices']
	] as const;
	const mlValues = [
		['Received', 'Confirmed by Morgan Lloyd', true],
		['RAS Applied', 'Awaiting confirmation', false],
		['RAS Received', 'Awaiting confirmation', false],
		['Paid to SEI', 'Awaiting confirmation', false]
	] as const;

	// Provenance badges: unconfirmed is a warning, evidenced is a pass. Semantic tokens, so
	// tenant branding drives them and dark mode follows from the one definition.
	const TBC_TONE = 'bg-warning-subtle text-warning-strong border-warning-line';
	const EVIDENCED_TONE = 'bg-success-subtle text-success-strong border-success-line';
</script>

<div class="space-y-6">
	<p class="text-muted-foreground max-w-3xl text-sm">
		How a contribution's progress is reported across Graphene, Morgan Lloyd and SEI.
	</p>

	<Alert>
		<AlertDescription>
			Graphene status is authoritative. Morgan Lloyd statuses marked
			<Badge variant="outline" class={TBC_TONE}>TBC</Badge>
			still need confirmation.
		</AlertDescription>
	</Alert>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Contribution status across systems</h4>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Workbench status</Table.Head>
						<Table.Head>Meaning</Table.Head>
						<Table.Head>Morgan Lloyd status</Table.Head>
						<Table.Head>Updated by</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each mlMap as row (row.status)}
						<Table.Row>
							<Table.Cell><StatusBadge status={row.status} /></Table.Cell>
							<Table.Cell class="text-xs">{row.meaning}</Table.Cell>
							<Table.Cell class="text-xs">
								{#if row.ml === '—' || row.ml.startsWith('Not in')}
									<span class="text-muted-foreground">{row.ml}</span>
								{:else}
									<code>{row.ml}</code>
									{#if row.tbc}<Badge variant="outline" class="{TBC_TONE} ml-1">TBC</Badge>
									{:else}<Badge variant="outline" class="{EVIDENCED_TONE} ml-1">evidenced</Badge
										>{/if}
								{/if}
							</Table.Cell>
							<Table.Cell class="font-mono text-[11px]">{row.by}</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		</div>
	</div>

	<div class="grid gap-4 lg:grid-cols-3">
		<div>
			<h4 class="mb-2 text-sm font-semibold">SEI activity status</h4>
			<div class="border-border/70 bg-card overflow-hidden rounded-xl border">
				<Table.Root>
					<Table.Header>
						<Table.Row
							><Table.Head class="text-right">Code</Table.Head><Table.Head>Description</Table.Head
							></Table.Row
						>
					</Table.Header>
					<Table.Body>
						{#each activityStatus as [code, desc] (code)}
							<Table.Row>
								<Table.Cell class="text-right font-mono text-xs">{code}</Table.Cell>
								<Table.Cell class="text-xs">{desc}</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			</div>
		</div>
		<div class="space-y-4">
			<div>
				<h4 class="mb-2 text-sm font-semibold">SEI request status</h4>
				<div class="border-border/70 bg-card overflow-hidden rounded-xl border">
					<Table.Root>
						<Table.Body>
							{#each requestStatus as [code, desc] (code)}
								<Table.Row>
									<Table.Cell class="text-right font-mono text-xs">{code}</Table.Cell>
									<Table.Cell class="text-xs">{desc}</Table.Cell>
								</Table.Row>
							{/each}
						</Table.Body>
					</Table.Root>
				</div>
			</div>
			<div>
				<h4 class="mb-2 text-sm font-semibold">Transaction flags</h4>
				<div class="border-border/70 bg-card overflow-hidden rounded-xl border">
					<Table.Root>
						<Table.Body>
							{#each flags as [name, desc] (name)}
								<Table.Row>
									<Table.Cell class="font-mono text-xs">{name}</Table.Cell>
									<Table.Cell class="text-xs">{desc}</Table.Cell>
								</Table.Row>
							{/each}
						</Table.Body>
					</Table.Root>
				</div>
			</div>
		</div>
		<div>
			<h4 class="mb-2 text-sm font-semibold">
				Morgan Lloyd status
				<Badge variant="outline" class={TBC_TONE}>provisional</Badge>
			</h4>
			<div class="border-border/70 bg-card overflow-hidden rounded-xl border">
				<Table.Root>
					<Table.Header>
						<Table.Row><Table.Head>Value</Table.Head><Table.Head>Confidence</Table.Head></Table.Row>
					</Table.Header>
					<Table.Body>
						{#each mlValues as [value, conf, ok] (value)}
							<Table.Row>
								<Table.Cell class="font-mono text-xs">{value}</Table.Cell>
								<Table.Cell class="text-xs">
									{conf}
									{#if ok}<Badge variant="outline" class="{EVIDENCED_TONE} ml-1">ok</Badge>
									{:else}<Badge variant="outline" class="{TBC_TONE} ml-1">TBC</Badge>{/if}
								</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			</div>
			<p class="text-muted-foreground mt-2 text-xs">
				Confirm provisional Morgan Lloyd statuses before relying on them operationally.
			</p>
		</div>
	</div>

	<div class="text-muted-foreground text-xs">
		Lifecycle:
		{#each STATUSES as s, i (s)}<StatusBadge status={s} />{#if i < STATUSES.length - 1}<span
					class="mx-1">→</span
				>{/if}{/each}
	</div>
</div>
