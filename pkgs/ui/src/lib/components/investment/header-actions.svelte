<script lang="ts" module>
	/** The portfolioHeader `actions` setting's closed set (block-contract.json). */
	export type HeaderActionsMode = 'deposit_withdraw' | 'contact' | 'none';

	/**
	 * A stored `actions` setting, defensively normalised: settings ride the wire as untyped
	 * JSON, so junk degrades to the historic default (the Deposit/Withdraw pair — what every
	 * layout showed before the setting existed) rather than hiding a client's money buttons.
	 */
	export function normaliseActions(raw: unknown): HeaderActionsMode {
		return raw === 'contact' || raw === 'none' ? raw : 'deposit_withdraw';
	}

	/** Which of the pair to paint. A switched-off action is absent; any other mode paints neither. */
	export function shownMoneyButtons(
		mode: HeaderActionsMode,
		disabled: readonly string[] = []
	): { payment: boolean; withdraw: boolean } {
		if (mode !== 'deposit_withdraw') return { payment: false, withdraw: false };
		return {
			payment: !disabled.includes('transaction.payment'),
			withdraw: !disabled.includes('transaction.withdrawal')
		};
	}
</script>

<script lang="ts">
	// The portfolio header's action row, shared by the three big looks so the `actions` setting
	// is wired once. 'none' renders nothing AT ALL (the caller's layout must not reserve the
	// row); previews render the same pixels as inert spans — a preview button that navigates
	// would let the studio wander off into the live portal.
	import { cn } from '../../utils.js';

	let {
		mode = 'deposit_withdraw',
		disabled = [],
		tone = 'hero',
		interactive = true
	}: {
		mode?: HeaderActionsMode;
		/** Action keys the firm has switched off. A missing policy is not in the list. */
		disabled?: readonly string[];
		/** 'hero' styles for ink surfaces (hero/canvas); 'page' for the ledger's page ground. */
		tone?: 'hero' | 'page';
		interactive?: boolean;
	} = $props();

	const buttons = $derived(shownMoneyButtons(mode, disabled));

	// 36px visible + the ::before extension = a ≥44px touch target without a 44px-tall button.
	const base =
		'relative inline-flex h-9 items-center rounded-md px-4 text-[0.8rem] font-medium transition-colors ' +
		'before:absolute before:-inset-x-0.5 before:-inset-y-1 before:content-[""]';
	const brandFill = 'bg-[var(--brand)] text-[var(--brand-foreground)] hover:brightness-105';
</script>

{#snippet action(href: string, label: string, className: string, style?: string)}
	{#if interactive}
		<a {href} class={className} {style}>{label}</a>
	{:else}
		<span class={className} {style}>{label}</span>
	{/if}
{/snippet}

{#if mode === 'contact'}
	{@render action('/messages', 'Contact your adviser', cn(base, brandFill))}
{:else if buttons.payment || buttons.withdraw}
	<div class="flex gap-2">
		<!-- "Payment", the adviser platform's own word for this (book/accounts/instruction.ts
		     CASH_INSTRUCTION_TITLE) — a client told "payment in" while their adviser says
		     "payment" is reading two firms. The href keeps the `deposit` segment: it is the
		     route's name rather than anything anybody reads, and links to it are already out. -->
		{#if buttons.payment}
			{@render action('/transfer/deposit', 'Payment', cn(base, brandFill))}
		{/if}
		{#if buttons.withdraw}
			{#if tone === 'page'}
				{@render action(
					'/transfer/withdraw',
					'Withdraw',
					cn(base, 'border-border text-foreground hover:border-foreground/40 border')
				)}
			{:else}
				{@render action(
					'/transfer/withdraw',
					'Withdraw',
					cn(base, 'border'),
					'border-color: color-mix(in oklch, var(--hero-foreground) 25%, transparent); color: var(--hero-foreground)'
				)}
			{/if}
		{/if}
	</div>
{/if}
