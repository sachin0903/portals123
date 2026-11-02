<script lang="ts">
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		Checkbox,
		EmptyState,
		LINK_RECORD,
		LoadErrorBanner,
		Money,
		MoneyInput,
		StatusBadge,
		cn,
		formatGBP
	} from '@graphene/ui';
	import { enhance } from '$app/forms';
	import { invalidateAll } from '$app/navigation';
	import { formatDate } from '$lib/format';
	import { pendingLabel, type MoneyStep } from '$lib/money-movement';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import InfoIcon from '@lucide/svelte/icons/info';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import MessageCircleIcon from '@lucide/svelte/icons/message-circle';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	const isDeposit = $derived(data.kind === 'deposit');
	// "payment", matching the button that opens this page and the adviser platform's own word
	// for the same act. The route SEGMENT stays `deposit`; nobody reads it and links exist.
	const title = $derived(isDeposit ? 'Make a payment' : 'Take money out');
	const accounts = $derived(data.accounts);
	// Who to contact: the adviser, or the firm by name when nobody is assigned ($lib/adviser).
	const whom = $derived(data.contact.kind === 'firm' ? data.contact.name : 'your adviser');
	const firmName = $derived(data.contact.kind === 'firm' ? data.contact.name : data.contact.firm);

	// THE STEP IS THE LAST ACTION'S ANSWER. `preview` answers with the review, `confirm` with the
	// done screen, a failure with the step to return to — so a reload, or a browser without JS,
	// lands on the same screen and there is no amount held in client state to lose or to trust.
	const step = $derived<MoneyStep>(form?.step ?? 'amount');
	const preview = $derived(form?.step === 'review' ? form.preview : null);
	const raised = $derived(form?.step === 'done' ? form.raised : null);
	const failure = $derived(form?.error ?? null);

	// What the form last posted, so a refusal ("Only £870 can be withdrawn…") keeps the client's
	// choices in place rather than asking again from scratch.
	let selected = $state<string | null>(null);
	let amount = $state<number | null>(null);
	let understood = $state(false);
	$effect(() => {
		selected = form?.account_uid ?? preview?.account.uid ?? data.preselected;
		amount = form?.amount ?? preview?.amount ?? null;
	});
	$effect(() => {
		// A fresh review starts unticked; the declaration is made per request, not remembered.
		if (step === 'review') understood = false;
	});
	let busy = $state(false);
	let top = $state<HTMLElement | null>(null);
	// A step change is a new screen: the heading and the progress strip belong at the top of it,
	// not wherever the Continue button happened to sit when it was pressed. `scrollIntoView` on
	// the page's own top, not `window.scrollTo`: the shell scrolls a nested container.
	const advance = () => {
		busy = true;
		return async ({ update }: { update: (o?: { reset?: boolean }) => Promise<void> }) => {
			await update({ reset: false });
			busy = false;
			top?.scrollIntoView({ block: 'start', behavior: 'smooth' });
		};
	};

	const steps = $derived([
		{ id: 'amount', label: 'Amount' },
		{
			id: 'review',
			label:
				!isDeposit && (preview?.review_required || raised?.review_required)
					? 'Safety check'
					: 'Review'
		},
		{ id: 'done', label: 'Done' }
	] as const);
	const stepIndex = $derived(steps.findIndex((s) => s.id === step));

	// A button reads "Add my £500", not "£500.00": the pence are on the summary row above it.
	const spoken = (n: number) =>
		Number.isInteger(n) ? formatGBP(n).replace(/\.00$/, '') : formatGBP(n);
	// "0.6%" below ten per cent, "12%" above: a withdrawal that is a sliver of the account should
	// not round up to a whole point and read as more than it is.
	const pct = (f: number | null | undefined) =>
		f == null ? null : `${f < 0.1 ? (f * 100).toFixed(1) : Math.round(f * 100)}%`;
	const confirmLabel = $derived(
		!preview
			? ''
			: preview.review_required
				? 'Send for review'
				: isDeposit
					? `Add my ${spoken(preview.amount)}`
					: `Take out ${spoken(preview.amount)}`
	);
	// Links back to this page. A full reload (`data-sveltekit-reload`) rather than a client-side
	// navigation, because the step IS the last action's result and only a fresh request clears it.
	const here = $derived(`/transfer/${data.routeKind}`);
	const restartHref = $derived(here + (selected ? `?account=${encodeURIComponent(selected)}` : ''));
	// A pending request this client raised themselves is theirs to withdraw; one the firm raised
	// on their behalf is listed so nothing is hidden, but is the firm's to change.
	const pending = $derived(data.pending);
</script>

<div class="mx-auto max-w-lg space-y-6" bind:this={top}>
	<div>
		<h1 class="text-3xl font-semibold tracking-tight">{title}</h1>
		<p class="text-muted-foreground mt-1">
			{isDeposit ? 'Add money to one of your accounts.' : 'Withdraw from one of your accounts.'}
		</p>
	</div>

	<!-- Where you are. Three steps, named by what each asks for; the middle one becomes a safety
	     check when a withdrawal trips a threshold, so the client sees why there is a pause. -->
	<ol class="flex items-center gap-2 text-xs" aria-label="Progress">
		{#each steps as s, i (s.id)}
			<li
				class={cn(
					'flex items-center gap-1.5',
					i <= stepIndex ? 'text-foreground font-medium' : 'text-muted-foreground'
				)}
				aria-current={i === stepIndex ? 'step' : undefined}
			>
				<span
					class={cn(
						'grid size-5 shrink-0 place-items-center rounded-full border text-[11px] tabular-nums',
						i < stepIndex
							? 'bg-brand text-brand-foreground border-brand'
							: i === stepIndex
								? 'border-brand text-brand-strong bg-brand-subtle'
								: 'border-border'
					)}
				>
					{#if i < stepIndex}<CheckIcon class="size-3" />{:else}{i + 1}{/if}
				</span>
				{s.label}
			</li>
			{#if i < steps.length - 1}
				<li aria-hidden="true" class="bg-border h-px min-w-4 flex-1"></li>
			{/if}
		{/each}
	</ol>

	{#if data.loadError}
		<LoadErrorBanner
			message="We couldn’t load your accounts. Please try again."
			onRetry={() => invalidateAll()}
		/>
	{/if}

	{#if failure}
		<Alert variant="destructive">
			<TriangleAlertIcon class="size-4" />
			<AlertDescription>{failure}</AlertDescription>
		</Alert>
	{/if}

	{#if step === 'amount'}
		{#if accounts.length}
			<form method="POST" action="?/preview" class="space-y-6" use:enhance={advance}>
				<Card glass>
					<CardHeader>
						<CardTitle>{isDeposit ? 'Which account?' : 'From which account?'}</CardTitle>
					</CardHeader>
					<CardContent>
						<!-- Native radios, so the choice posts without JS; the card is the hit area. -->
						<fieldset class="space-y-2">
							<legend class="sr-only">Account</legend>
							{#each accounts as account (account.id)}
								<label
									class={cn(
										'flex cursor-pointer items-center justify-between gap-3 rounded-xl border px-3 py-2.5 transition-colors',
										'has-checked:border-brand has-checked:bg-brand-subtle/50 hover:bg-muted/50'
									)}
								>
									<span class="flex min-w-0 items-center gap-3">
										<input
											type="radio"
											name="account"
											value={account.uid}
											checked={selected === account.uid}
											onchange={() => (selected = account.uid)}
											class="accent-brand size-4 shrink-0"
											required
										/>
										<span class="min-w-0">
											<span class="block truncate text-sm font-medium">
												{account.name ?? account.product_name ?? 'Account'}
											</span>
											{#if account.product_type}
												<span class="text-muted-foreground block text-xs"
													>{account.product_type}</span
												>
											{/if}
										</span>
									</span>
									<span class="text-sm tabular-nums"><Money amount={account.value ?? 0} /></span>
								</label>
							{/each}
						</fieldset>
					</CardContent>
				</Card>

				<Card glass>
					<CardHeader>
						<CardTitle>{isDeposit ? 'How much are you adding?' : 'How much?'}</CardTitle>
					</CardHeader>
					<CardContent class="space-y-4">
						<div class="space-y-1.5">
							<label for="amount" class="text-sm font-medium">Amount</label>
							<MoneyInput id="amount" name="amount" bind:value={amount} min={0} required />
						</div>
						<Alert variant="info">
							<InfoIcon class="size-4" />
							<AlertDescription>
								{#if isDeposit}
									Once your payment arrives we’ll put it to work in this account. You’ll see where
									to send it, and your reference, at the end.
								{:else}
									We take it from cash first and only sell investments if we have to. Larger amounts
									get a quick check from {firmName ?? 'your firm'} before anything moves.
								{/if}
							</AlertDescription>
						</Alert>
					</CardContent>
				</Card>

				<div class="flex flex-wrap gap-2">
					<Button type="submit" disabled={busy || !selected || !amount}>Continue</Button>
					<Button href="/" variant="outline">Back to home</Button>
				</div>
			</form>
		{:else if !data.loadError}
			<Card glass>
				<CardContent>
					<EmptyState
						compact
						icon={WalletIcon}
						title="No accounts yet"
						description="Once your firm opens an account for you it will appear here."
					/>
				</CardContent>
			</Card>
		{/if}

		{#if pending.length}
			<Card glass>
				<CardHeader>
					<CardTitle>Waiting to happen</CardTitle>
				</CardHeader>
				<CardContent>
					<ul class="divide-border divide-y">
						{#each pending as req (req.uid)}
							<li class="flex items-center justify-between gap-3 py-2.5 first:pt-0 last:pb-0">
								<div class="min-w-0">
									<p class="flex flex-wrap items-center gap-2 text-sm font-medium">
										{pendingLabel(req.kind)} · <Money amount={req.amount} />
										{#if req.review_required}
											<StatusBadge tone="warning">Being checked</StatusBadge>
										{/if}
									</p>
									<p class="text-muted-foreground text-xs">
										<a href="/accounts/{req.account_uid}" class={LINK_RECORD}>{req.account_name}</a>
										{#if req.due_date}
											· {req.kind === 'expected_in' ? 'expected by' : 'due'}
											{formatDate(req.due_date)}
										{/if}
									</p>
								</div>
								{#if req.raised_by_client}
									<form
										method="POST"
										action="?/cancel"
										use:enhance={() => {
											return async ({ update }) => {
												await update({ reset: false });
												await invalidateAll();
											};
										}}
									>
										<input type="hidden" name="uid" value={req.uid} />
										<Button type="submit" variant="ghost" size="sm">Cancel</Button>
									</form>
								{/if}
							</li>
						{/each}
					</ul>
				</CardContent>
			</Card>
		{/if}
	{:else if step === 'review' && preview}
		<form method="POST" action="?/confirm" class="space-y-6" use:enhance={advance}>
			<input type="hidden" name="account" value={preview.account.uid} />
			<input type="hidden" name="amount" value={preview.amount} />

			{#if preview.review_required}
				<Alert variant="warning">
					<ShieldCheckIcon class="size-4" />
					<AlertTitle>Let’s just check this one with you</AlertTitle>
					<AlertDescription>
						<p>
							This is a large withdrawal for this account, so {firmName ?? 'your firm'} will have a quick
							look and call you before anything moves. It’s a safety step, not a refusal.
						</p>
						<ul class="mt-2 flex flex-wrap gap-1.5">
							{#each preview.flags as flag (flag.code)}
								<li><StatusBadge tone="warning">{flag.label}</StatusBadge></li>
							{/each}
						</ul>
					</AlertDescription>
				</Alert>
			{/if}

			<Card glass>
				<CardHeader>
					<CardTitle>{isDeposit ? 'Your payment' : 'Your withdrawal'}</CardTitle>
				</CardHeader>
				<CardContent>
					<dl class="divide-border divide-y text-sm">
						{#snippet row(label: string, value: string | { amount: number } | null)}
							{#if value !== null}
								<div class="flex justify-between gap-4 py-2 first:pt-0 last:pb-0">
									<dt class="text-muted-foreground">{label}</dt>
									<dd class="text-right font-medium tabular-nums">
										{#if typeof value === 'string'}{value}{:else}<Money
												amount={value.amount}
											/>{/if}
									</dd>
								</div>
							{/if}
						{/snippet}
						{@render row(isDeposit ? 'Into' : 'From', preview.account.name)}
						{@render row('Amount', { amount: preview.amount })}
						{#if !isDeposit && preview.instruction_kind === 'model_withdrawal'}
							{@render row(
								'From cash',
								preview.from_cash > 0 ? { amount: preview.from_cash } : null
							)}
							{@render row('From selling investments', { amount: preview.from_investments })}
						{/if}
						{#if !isDeposit}
							{@render row('Paid to', preview.bank ?? 'Your nominated bank account')}
							<!-- No date on a flagged one: it lands once the firm has released it. -->
							{@render row(
								'Should land by',
								preview.lands_by && !preview.review_required ? formatDate(preview.lands_by) : null
							)}
						{:else}
							{@render row(
								'Expected by',
								preview.expected_by ? formatDate(preview.expected_by) : null
							)}
						{/if}
						{#if preview.allowance_after !== null}
							{@render row('ISA allowance left after', { amount: preview.allowance_after })}
						{/if}
					</dl>
				</CardContent>
			</Card>

			<Card glass>
				<CardHeader>
					<CardTitle>Here’s what changes</CardTitle>
				</CardHeader>
				<CardContent>
					<ul class="text-muted-foreground space-y-1.5 text-sm">
						<li>
							{preview.account.name} goes from
							<span class="text-foreground font-medium tabular-nums">
								<Money amount={preview.account.value} />
							</span>
							to
							<span class="text-foreground font-medium tabular-nums">
								<Money amount={preview.value_after} />
							</span>.
						</li>
						{#if isDeposit}
							<li>
								{#if preview.account.has_model}
									It’s invested in line with your account’s portfolio within
									{preview.invest_within_working_days ?? 3} working days of arriving.
								{:else}
									It sits as cash in the account until you choose what to do with it.
								{/if}
							</li>
						{:else}
							{#if pct(preview.share_of_account)}
								<li>That’s {pct(preview.share_of_account)} of this account.</li>
							{/if}
							{#if pct(preview.share_of_portfolio)}
								<li>And {pct(preview.share_of_portfolio)} of everything you hold with us.</li>
							{/if}
							{#if preview.instruction_kind === 'model_withdrawal'}
								<li>Selling investments can take a few days to settle before the money leaves.</li>
							{/if}
						{/if}
					</ul>
				</CardContent>
			</Card>

			<Alert variant="info">
				<InfoIcon class="size-4" />
				<AlertTitle>This is your decision, not financial advice</AlertTitle>
				<AlertDescription>
					{isDeposit
						? 'Investments can go down as well as up, and you may get back less than you put in.'
						: 'Taking money out means it’s no longer invested, and selling can crystallise a gain or a loss.'}
					If you’d like to talk it through first,
					<a href="/messages" class={LINK_RECORD}>message {whom}</a>.
				</AlertDescription>
			</Alert>

			<label class="flex items-start gap-2.5 text-sm">
				<Checkbox bind:checked={understood} />
				<span>I’ve read this and I want to go ahead.</span>
			</label>
			{#if understood}<input type="hidden" name="understood" value="on" />{/if}

			<div class="flex flex-wrap gap-2">
				<Button type="submit" disabled={busy || !understood}>{confirmLabel}</Button>
				<Button href={restartHref} variant="outline" data-sveltekit-reload>Change amount</Button>
			</div>
		</form>
	{:else if step === 'done' && raised}
		<Card glass>
			<CardContent class="space-y-5">
				<div class="flex items-start gap-3">
					<span
						class={cn(
							'grid size-10 shrink-0 place-items-center rounded-full',
							raised.review_required || raised.held
								? 'bg-warning-subtle text-warning-strong'
								: 'bg-success-subtle text-success-strong'
						)}
					>
						{#if raised.review_required || raised.held}
							<ShieldCheckIcon class="size-5" />
						{:else}
							<CheckIcon class="size-5" />
						{/if}
					</span>
					<div class="min-w-0">
						<h2 class="text-lg font-semibold">
							{#if raised.review_required || raised.held}
								Sent to {firmName ?? 'your firm'}
							{:else if isDeposit}
								Payment expected
							{:else}
								Withdrawal requested
							{/if}
						</h2>
						<p class="text-muted-foreground mt-1 text-sm">
							{#if raised.review_required}
								A member of the team will call you before anything moves. If you’d rather not wait, <a
									href="/messages"
									class={LINK_RECORD}>message {whom}</a
								>.
							{:else if raised.held}
								This is waiting for approval before anything moves. If you’d rather not wait, <a
									href="/messages"
									class={LINK_RECORD}>message {whom}</a
								>.
							{:else if isDeposit}
								We’re expecting {formatGBP(raised.instruction.amount)} into
								{raised.preview.account.name}. Send it with the reference below so it reaches the
								right account.
							{:else}
								{formatGBP(raised.instruction.amount)} from {raised.preview.account.name}
								should land in {raised.preview.bank ?? 'your nominated bank account'}
								{#if raised.preview.lands_by}by {formatDate(raised.preview.lands_by)}{/if}.
							{/if}
						</p>
					</div>
				</div>

				{#if isDeposit && !raised.review_required && !raised.held}
					<div class="bg-muted/50 rounded-xl border p-4">
						<p class="text-muted-foreground text-xs font-medium uppercase tracking-wide">
							Send the payment to
						</p>
						<dl class="mt-2 space-y-1.5 text-sm">
							{#if raised.pay_in}
								{#each Object.entries(raised.pay_in) as [label, value] (label)}
									<div class="flex justify-between gap-4">
										<dt class="text-muted-foreground">{label}</dt>
										<dd class="font-medium tabular-nums">{value}</dd>
									</div>
								{/each}
							{:else}
								<!-- No firm has pay-in details on the platform yet (API module note). Until it
								     does, the firm confirms where to send it — the reference is the part that
								     ties the money to the account whatever bank it goes to. -->
								<p class="text-muted-foreground">
									{firmName ?? 'Your firm'} will confirm the bank details to pay into. Quote this reference
									when you do.
								</p>
							{/if}
							<div class="flex justify-between gap-4">
								<dt class="text-muted-foreground">Reference</dt>
								<dd class="font-mono text-base font-semibold tracking-wide select-all">
									{raised.instruction.uid}
								</dd>
							</div>
						</dl>
					</div>
				{/if}

				<p class="text-muted-foreground text-xs">
					You can cancel this while it’s still waiting — it’s listed under “Waiting to happen” on
					this page.
				</p>
			</CardContent>
		</Card>

		<div class="flex flex-wrap gap-2">
			<!-- The account the money is moving on, not a listing: the portal's default page set
			     has no /accounts page (accounts live on Portfolio), and this client has just named
			     the account anyway. -->
			<Button href="/accounts/{raised.preview.account.uid}"
				>Back to {raised.preview.account.name}</Button
			>
			<Button href={here} variant="outline" data-sveltekit-reload>
				{isDeposit ? 'Make another payment' : 'Take out more'}
			</Button>
		</div>
	{/if}

	{#if step === 'amount'}
		<p class="text-muted-foreground text-sm">
			Not sure? <a href="/messages" class={LINK_RECORD}
				><MessageCircleIcon class="inline size-3.5" /> Contact {whom}</a
			>
			before you go ahead.
		</p>
	{/if}
</div>
