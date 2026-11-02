<script lang="ts">
	// Action-specific body of a review: a bank/model edit is now vs proposed; a money
	// movement is the instruction itself. The target (client, account) sits above.
	//
	// A card that resends every field lists rows that do not move. Those FOLD: the rows that
	// change come first, and the rest sit behind "Show 5 unchanged" — RV-3 was six rows of which
	// one moved, and the reviewer had to find it. Once a case is approved the columns read Was /
	// Now, because the proposed side is what the record now says.
	import { LINK_RECORD } from '../../../text-shell.js';
	import { Button } from '../../ui/button/index.js';
	import { Money } from '../../ui/money/index.js';
	import * as Table from '../../ui/table/index.js';
	import { reviewAccount, reviewChangeView, reviewSubject } from './reviews.js';
	import type { ReviewRow } from './types.js';

	let {
		review,
		clientHref = null,
		accountHref = null,
		showClient = true,
		showAccount = true,
		showTitle = true,
		fold = true
	}: {
		review: ReviewRow;
		clientHref?: string | null;
		accountHref?: string | null;
		/** Queue and detail always name the client. A record card already is the client. */
		showClient?: boolean;
		/** Hide on the account record itself. The queue, detail, and the client record keep it. */
		showAccount?: boolean;
		/** The body's own heading ("Client details"). Off where the row above already says it. */
		showTitle?: boolean;
		/** Fold the rows a resent card did not change behind a count. Off to list every row. */
		fold?: boolean;
	} = $props();

	const view = $derived(reviewChangeView(review));
	const client = $derived(reviewSubject(review));
	const account = $derived(reviewAccount(review));

	let unfolded = $state(false);
	const diffRows = $derived(view.kind === 'diff' ? view.rows : []);
	const moved = $derived(diffRows.filter((line) => line.changed !== false));
	const still = $derived(diffRows.filter((line) => line.changed === false));
	const folded = $derived(fold && still.length > 0 && !unfolded);
	const shown = $derived(folded ? moved : [...moved, ...still]);
	// Once applied, "proposed" is what the record says now.
	const applied = $derived(review.status === 'approved');
</script>

<div class="min-w-0 space-y-3">
	{#if (showClient && (review.contact_name || review.contact_uid)) || (showAccount && account)}
		<dl class="grid grid-cols-2 gap-x-6 gap-y-2 text-sm">
			{#if showClient && (review.contact_name || review.contact_uid)}
				<div class="min-w-0">
					<dt class="text-muted-foreground text-xs">Client</dt>
					<dd class="mt-0.5">
						{#if clientHref}
							<a href={clientHref} class={LINK_RECORD}>{client}</a>
						{:else}
							{client}
						{/if}
					</dd>
				</div>
			{/if}
			{#if showAccount && account}
				<div class="min-w-0">
					<dt class="text-muted-foreground text-xs">Account</dt>
					<dd class="mt-0.5">
						{#if accountHref}
							<a href={accountHref} class={LINK_RECORD}>{account}</a>
						{:else}
							{account}
						{/if}
					</dd>
				</div>
			{/if}
		</dl>
	{/if}

	{#if showTitle}
		<p class="flex flex-wrap items-baseline gap-x-2 text-sm font-medium">
			{view.title}
			{#if view.kind === 'diff' && still.length}
				<span class="text-muted-foreground text-xs font-normal">
					{moved.length} of {diffRows.length}
					{diffRows.length === 1 ? 'field' : 'fields'} change{moved.length === 1 ? 's' : ''}
				</span>
			{/if}
		</p>
	{/if}
	{#if view.kind === 'facts' && view.caption}
		<p class="text-muted-foreground text-xs">{view.caption}</p>
	{/if}

	{#if view.kind === 'diff'}
		<Table.Root aria-label={view.title}>
			<Table.Header>
				<Table.Row>
					<Table.Head>Field</Table.Head>
					<Table.Head>{applied ? 'Was' : 'Now'}</Table.Head>
					<Table.Head>{applied ? 'Now' : 'Proposed'}</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each shown as diff (diff.field)}
					{@const same = diff.changed === false}
					<!-- A row that does not move sits muted so the eye lands on the ones that do. -->
					<Table.Row class={same ? 'text-muted-foreground' : undefined}>
						<Table.Cell class="text-muted-foreground py-1.5 text-xs whitespace-nowrap"
							>{diff.field}</Table.Cell
						>
						<Table.Cell class="py-1.5 break-words whitespace-normal tabular-nums"
							>{diff.now}</Table.Cell
						>
						<Table.Cell
							class="py-1.5 break-words whitespace-normal tabular-nums {same ? '' : 'font-medium'}"
							>{diff.proposed}</Table.Cell
						>
					</Table.Row>
				{/each}
				{#if fold && still.length}
					<Table.Row class="hover:bg-transparent">
						<Table.Cell colspan={3} class="py-1">
							<Button
								type="button"
								variant="link"
								size="sm"
								class="h-6 px-0 text-xs"
								aria-expanded={unfolded}
								onclick={() => (unfolded = !unfolded)}
							>
								{`${unfolded ? 'Hide' : 'Show'} ${still.length} unchanged`}
							</Button>
							{#if !unfolded}
								<span class="text-muted-foreground ml-2 text-xs">
									{still.map((line) => line.field).join(', ')}
								</span>
							{/if}
						</Table.Cell>
					</Table.Row>
				{/if}
			</Table.Body>
		</Table.Root>
	{:else if view.rows.length === 0}
		<p class="text-muted-foreground text-xs">No further detail was recorded.</p>
	{:else}
		<dl class="grid min-w-0 grid-cols-2 gap-x-6 text-xs sm:grid-cols-3">
			{#each view.rows as fact (fact.label)}
				<div class="border-border/50 min-w-0 border-b py-1.5">
					<dt class="text-muted-foreground text-xs">{fact.label}</dt>
					<dd class="mt-0.5 {fact.label === 'Amount' ? 'tabular-nums' : ''}">
						{#if fact.money}<Money amount={fact.money.amount} />{:else}{fact.value}{/if}
					</dd>
				</div>
			{/each}
		</dl>
	{/if}
</div>
