<script lang="ts">
	import { Card, Table } from '@graphene/ui';
	import { STATUSES } from '$lib/contributions/status';
	import StatusBadge from './StatusBadge.svelte';

	// The step accent is wayfinding by actor, mapped onto branded semantic tokens so a tenant's
	// palette drives it: inbound money → info, our own steps → brand, awaiting HMRC → warning,
	// relief landed at the member → success.
	//
	// `accent` is a theme CSS var applied inline, not a `border-t-*` utility: `.glass-panel` sets
	// the `border` shorthand after Tailwind's utility layer, so a top accent on a glass Card is
	// otherwise flattened to the 1px glass hairline. `ink` is a normal utility class.
	const steps = [
		{
			n: 1,
			actor: 'Client / Adviser → SEI',
			accent: '--info',
			ink: 'text-info-strong',
			text: 'The client or adviser sends funds to the SEI account. The adviser records the expected payment and confirms who is paying.',
			api: ''
		},
		{
			n: 2,
			actor: 'Graphene (detect)',
			accent: '--brand',
			ink: 'text-brand-strong',
			text: 'Graphene checks the daily SEI credits and adds each payment to the classification queue.',
			api: 'SEI transaction feed'
		},
		{
			n: 3,
			actor: 'Graphene → Morgan Lloyd',
			accent: '--brand',
			ink: 'text-brand-strong',
			text: "Graphene records the classified contribution against the member's Morgan Lloyd scheme.",
			api: 'Morgan Lloyd contribution'
		},
		{
			n: 4,
			actor: 'Morgan Lloyd',
			accent: '--warning',
			ink: 'text-warning-strong',
			text: 'ML records the contribution and reclaims basic-rate tax relief from HMRC where necessary (personal only; employer is gross).',
			api: 'HMRC relief at source'
		},
		{
			n: 5,
			actor: 'Morgan Lloyd → SEI',
			accent: '--success',
			ink: 'text-success-strong',
			text: 'Once relief is received, Morgan Lloyd sends the payment and member breakdown. Graphene allocates it to the relevant SEI accounts.',
			api: 'SEI cash receipt'
		}
	];
	const rules = [
		'Employer contributions are paid gross. No relief is reclaimed.',
		'Personal contributions are made net of basic-rate relief; the scheme claims the relief at 20% from HMRC.'
	];
	const apiMap = [
		['Adviser sets expectation', 'Graphene', 'Record the expected contribution'],
		['Detect credit', 'SEI', 'Read the transaction feed'],
		['Find member and scheme', 'Morgan Lloyd', 'Find the member scheme'],
		['Record contribution', 'Morgan Lloyd', 'Create the contribution'],
		['Update contribution status', 'Morgan Lloyd', 'Update the contribution'],
		['Read recorded contributions', 'Morgan Lloyd', 'Check received contributions'],
		['Post relief to SEI', 'SEI', 'Allocate the cash receipt']
	] as const;
</script>

<div class="space-y-6">
	<div>
		<h4 class="mb-2 text-sm font-semibold">Contribution flow: five steps</h4>
		<div class="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
			{#each steps as step (step.n)}
				<Card glass class="p-4" style="border-top: 4px solid var({step.accent})">
					<div class="flex items-center gap-2">
						<span
							class="bg-foreground text-background flex size-5 items-center justify-center rounded-full text-xs font-bold"
							>{step.n}</span
						>
						<span class="text-xs font-semibold {step.ink}">{step.actor}</span>
					</div>
					<p class="mt-2 text-xs">{step.text}</p>
					{#if step.api}
						<code
							class="mt-2 inline-block rounded bg-brand/10 px-1.5 py-0.5 text-[10.5px] text-brand"
							>{step.api}</code
						>
					{/if}
				</Card>
			{/each}
		</div>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">Status lifecycle</h4>
		<div class="flex flex-wrap items-center gap-1">
			{#each STATUSES as s, i (s)}
				<StatusBadge status={s} />{#if i < STATUSES.length - 1}<span
						class="text-muted-foreground mx-0.5">→</span
					>{/if}
			{/each}
		</div>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">HMRC classification rules</h4>
		<ul class="space-y-1.5">
			{#each rules as rule (rule)}
				<li class="flex gap-2 text-sm">
					<span class="text-brand-strong">§</span><span>{rule}</span>
				</li>
			{/each}
		</ul>
	</div>

	<div>
		<h4 class="mb-2 text-sm font-semibold">System responsibilities</h4>
		<div class="border-border/70 bg-card overflow-x-auto rounded-xl border">
			<Table.Root>
				<Table.Header>
					<Table.Row>
						<Table.Head>Action</Table.Head>
						<Table.Head>System</Table.Head>
						<Table.Head>Task</Table.Head>
					</Table.Row>
				</Table.Header>
				<Table.Body>
					{#each apiMap as [action, system, op] (action)}
						<Table.Row>
							<Table.Cell class="text-xs font-medium">{action}</Table.Cell>
							<Table.Cell class="text-xs">{system}</Table.Cell>
							<Table.Cell><code class="text-[11px]">{op}</code></Table.Cell>
						</Table.Row>
					{/each}
				</Table.Body>
			</Table.Root>
		</div>
	</div>
</div>
