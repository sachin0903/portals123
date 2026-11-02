<script lang="ts">
	import { Alert, AlertDescription, Table } from '@graphene/ui';

	// §02 crosswalk: canonical Graphene stage → what each system reports.
	const crosswalk: { stage: string; ml: string; sei: string; seccl: string }[] = [
		{ stage: 'Draft · entered', ml: '—', sei: '—', seccl: 'Pending (drafted, not Placed)' },
		{
			stage: 'Submitted · submitted_to_ml',
			ml: 'submitted (cam_createtransfer)',
			sei: '—',
			seccl: 'Placed / With Previous Provider'
		},
		{
			stage: 'In flight · in_flight',
			ml: 'inflight (cam_status: processing)',
			sei: '2 Submitted · 3 Received at counterparty',
			seccl: 'Onboarding Assets (in-specie · CREST)'
		},
		{
			stage: 'Ceding complete · ml_complete',
			ml: 'complete / settled',
			sei: '—',
			seccl: 'Completed (ceding side done)'
		},
		{
			stage: 'Settlement advised',
			ml: 'settlement advised (cam_settlementdate)',
			sei: '—',
			seccl: '—'
		},
		{
			stage: 'Cash receipt · sei_acr_created',
			ml: '—',
			sei: 'cash · createCurrencyReceipt · assets · 8/9 In progress',
			seccl: '—'
		},
		{
			stage: 'Reconciled',
			ml: '—',
			sei: '6 Settled · 17 Cost basis matched',
			seccl: 'transaction posted'
		},
		{ stage: 'Closed · adviser_notified', ml: '—', sei: '7 Complete', seccl: '—' },
		{
			stage: 'Needs attention · manual_intervention',
			ml: 'retries exhausted',
			sei: '1 Action required · 10 Exception · 9 Cancelled',
			seccl: 'Rejected'
		}
	];

	const seiTransfer = [
		['1', 'Action required'],
		['2', 'In process: submitted to counterparty'],
		['3', 'In process: received at counterparty'],
		['4', 'Settled: awaiting cost basis'],
		['6', 'Settled'],
		['7', 'Complete'],
		['9', 'Cancelled'],
		['10', 'Valuation requested'],
		['18', 'Delete']
	];
	const seiAsset = [
		['8 / 9', 'In progress / In process'],
		['10', 'Exception'],
		['11', 'Complete'],
		['15 / 16', 'Awaiting / requested cost basis'],
		['17', 'Cost basis received & matched'],
		['13', 'Cancelled']
	];
	const mlMap = [
		['complete / completed / settled', '→ ml_complete'],
		['inflight / processing / submitted', '→ in_flight'],
		['unknown', '→ no advance (re-poll)']
	];
	const secclStatus = [
		['status', 'Pending · Placed · Completed · Deleted'],
		['transferStatus', 'With Previous Provider · Onboarding Assets · Completed · Rejected'],
		['Product Transfer', 'Cash · In Specie · movementType In/Out']
	];
</script>

<div class="space-y-6">
	<Alert>
		<AlertDescription class="text-xs">
			Graphene uses one transfer lifecycle while each provider reports progress in its own terms.
			This reference shows how those statuses correspond.
		</AlertDescription>
	</Alert>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Status crosswalk</h4>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Canonical stage</Table.Head>
						<Table.Head>Morgan Lloyd</Table.Head>
						<Table.Head>SEI</Table.Head>
						<Table.Head>Seccl</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each crosswalk as r (r.stage)}
						<Table.Row>
							<Table.Cell class="text-xs font-medium">{r.stage}</Table.Cell>
							<Table.Cell class="text-muted-foreground text-[11px]">{r.ml}</Table.Cell>
							<Table.Cell class="text-muted-foreground text-[11px]">{r.sei}</Table.Cell>
							<Table.Cell class="text-muted-foreground text-[11px]">{r.seccl}</Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		</div>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Provider statuses</h4>
		<div class="grid gap-4 lg:grid-cols-3">
			<div class="border-border/70 bg-card rounded-xl border p-3">
				<div class="mb-2 text-xs font-semibold">Morgan Lloyd</div>
				{#each mlMap as [raw, mapped] (raw)}
					<div class="flex justify-between gap-2 py-1 text-[11px]">
						<code>{raw}</code><span class="text-muted-foreground">{mapped}</span>
					</div>
				{/each}
			</div>
			<div class="border-border/70 bg-card rounded-xl border p-3">
				<div class="mb-2 text-xs font-semibold">SEI transfer status</div>
				{#each seiTransfer as [code, label] (code)}
					<div class="flex gap-2 py-0.5 text-[11px]">
						<code class="w-10 shrink-0">{code}</code><span class="text-muted-foreground"
							>{label}</span
						>
					</div>
				{/each}
				<div class="text-muted-foreground mt-2 mb-1 text-[11px] font-semibold">
					SEI asset transfer status
				</div>
				{#each seiAsset as [code, label] (code)}
					<div class="flex gap-2 py-0.5 text-[11px]">
						<code class="w-10 shrink-0">{code}</code><span class="text-muted-foreground"
							>{label}</span
						>
					</div>
				{/each}
			</div>
			<div class="border-border/70 bg-card rounded-xl border p-3">
				<div class="mb-2 text-xs font-semibold">Seccl</div>
				{#each secclStatus as [k, v] (k)}
					<div class="py-1 text-[11px]">
						<code>{k}</code>
						<div class="text-muted-foreground">{v}</div>
					</div>
				{/each}
			</div>
		</div>
	</div>
</div>
