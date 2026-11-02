<script lang="ts">
	// The platform's account record: the shared surface (@graphene/ui book/accounts) plus
	// everything that has a side effect or a platform route behind it.
	//
	// A COMPONENT, not a page, because the record's three bands are three routes now (the sidebar
	// is the strip) and all three need the same header, the same drawers and the same triggers.
	// Mounting this from each section keeps one copy of that wiring; the pages are the section
	// name and a <title>.
	//
	// What stays app-side here is what cannot move into pkgs/ui:
	//  - the drawdown drawer (mints a one-time DCube SSO session via this route's +server.ts),
	//  - the Action required queue, which links into the contributions workbench,
	//  - the link builders (this app's route tree).
	// The shared component renders the record and the TRIGGERS; absent these props it would be
	// read-only — which is the admin portal's default posture.
	import { moneyNeedsReason } from '@graphene/ui/book/reviews';
	import {
		AccountDetail,
		Button,
		clientName,
		cn,
		InstructionSheet,
		ModelSheet,
		LoadErrorBanner,
		Sheet,
		asRegularMovements,
		isPensionProduct,
		toast,
		type AccountBookLinks,
		type AccountSection,
		type AccountSheetKind,
		type CatalogModel,
		type SheetSubmitBody
	} from '@graphene/ui';
	import ExternalLinkIcon from '@lucide/svelte/icons/external-link';
	import { TransferForm } from '@graphene/ui/book/transfers';
	import ActionRequired from '$lib/components/transactions/action-required.svelte';
	import { goto, invalidate, invalidateAll } from '$app/navigation';
	// The beneficiaries panel takes the app's own `enhance`, per the portal waterfall — the shared
	// component knows the shape of a form action and nothing about SvelteKit.
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { listingExportHref } from '$lib/export-auth';
	import type { AccountRecordData, MorganLloydPush } from '$lib/components/accounts/types';

	let { data, section = 'overview' }: { data: AccountRecordData; section?: AccountSection } =
		$props();

	/** Only a band whose content is a TABLE takes the viewport height (see the frame's comment). */
	const locksViewport = $derived(section !== 'overview');

	const a = $derived(data.account);
	const title = $derived(a.name ?? a.uid ?? 'Account');
	const owner = $derived(a.client.id == null ? null : clientName(a.client));

	const links: AccountBookLinks = $derived({
		account: (uid) => `/accounts/${uid}`,
		accountTransactions: (uid) => `/accounts/${uid}/transactions`,
		client: (uid) => `/clients/${uid}`,
		review: (uid) => `/reviews?open=${uid}`
	});
	const transactionPageHref = (pageNumber: number) =>
		pageHref(data.transactionQuery, pageNumber, page.url.searchParams);

	/** A search or filter change on the transactions band is a navigation, like the pager beside it:
	 *  the query lives in the URL so the view is shareable and the back button widens it again. */
	function ontransactionquery(next: ListingQuery): void {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, { keepFocus: true, noScroll: true });
	}

	/** Export carries whatever the band is currently filtered to.
	 *
	 *  THE UID, like every other link on this record (`links.account` above, the instructions and
	 *  drawdown posts below): the route is `/accounts/[uid]`, so the serial resolves against
	 *  `book.accounts.uid`, matches nothing, and the download 404s. It arrived as `.id` because
	 *  these two exports were written while the route was still `[id]`.
	 *
	 *  Absent without `listings:export`, which is what hides the button on both bands. */
	const transactionExportHref = $derived(
		listingExportHref(
			page.data.permissions,
			page.data.role,
			`/accounts/${data.account.uid}/transactions/export` + (page.url.search ?? '')
		)
	);
	const holdingsExportHref = $derived(
		listingExportHref(
			page.data.permissions,
			page.data.role,
			`/accounts/${data.account.uid}/holdings/export` + (page.url.search ?? '')
		)
	);

	let transferOpen = $state(false);
	let pushOpen = $state(false);
	let pushBusy = $state(false);
	let pushReport = $state<MorganLloydPush | null>(null);
	let pushError = $state<string | null>(null);
	let drawdownOpen = $state(false);
	let drawdownUrl = $state<string | null>(null);
	let drawdownError = $state<string | null>(null);
	let drawdownLoading = $state(false);
	let instructionKind = $state<AccountSheetKind | null>(null);
	let modelOpen = $state(false);
	let models = $state<CatalogModel[]>([]);
	let modelsInflight = false;

	// Payment, Withdrawal and Ad hoc fee are REACHABLE from this record — Invest and Sell
	// (dealing against the model) are not; the client record still offers those.
	function openInstruction(kind: AccountSheetKind) {
		// A SIPP does not pay cash away from this record. The cash Withdraw button is hidden on
		// a pension; if anything still asks for one, mint a DCube session only when this scheme
		// is Morgan Lloyd-administered (`canDrawdown`). `cam_drawdownsetup` is the later income
		// instruction and is not this button.
		if (kind === 'withdrawal' && data.canDrawdown) {
			void openDrawdown();
			return;
		}
		instructionKind = kind;
	}

	async function openModel() {
		modelOpen = true;
		if (models.length || modelsInflight) return;
		modelsInflight = true;
		try {
			const res = await fetch('/pickers/models');
			if (res.ok) models = (await res.json()) as CatalogModel[];
		} catch {
			models = [];
		}
		modelsInflight = false;
	}

	async function saveInstruction(body: SheetSubmitBody): Promise<string | null> {
		if (body.kind === 'regular_withdrawal' && (a.is_pension ?? isPensionProduct(a.product_type))) {
			return 'Regular withdrawals cannot be set on a pension.';
		}
		if (body.kind === 'regular_payment' || body.kind === 'regular_withdrawal') {
			const row = {
				amount: body.amount,
				frequency: body.frequency,
				next_date: body.next_date,
				method: null,
				reference: null
			};
			const payments = asRegularMovements(a.payments);
			const withdrawals = asRegularMovements(a.withdrawals);
			return saveRecord(`/accounts/${a.uid}/regular-movements`, 'PUT', {
				payments: body.kind === 'regular_payment' ? [...payments, row] : payments,
				withdrawals: body.kind === 'regular_withdrawal' ? [...withdrawals, row] : withdrawals,
				...(body.justification ? { justification: body.justification } : {})
			});
		}
		// A SIPP pay-in IS a contribution: route it through the contributions path so it lands as a
		// pre-classified row in the contributions workbench (personal/employer), raises its own SEI
		// ACR, and follows settle → confirm classification → Morgan Lloyd — rather than the generic
		// instruction path, which never creates the contribution record the workbench reads. The
		// category is only present on a pension pay-in (the drawer asks it for a SIPP).
		if (body.kind === 'expected_in' && body.category) {
			const paidBy = body.category === 'employer_contribution' ? 'employer' : 'client';
			const contribRes = await fetch(`/accounts/${a.uid}/contributions`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					category: body.category,
					amount: body.amount,
					frequency: 'single',
					paid_by: paidBy,
					due_date: body.due_date ?? null
				})
			});
			const contrib = await contribRes.json().catch(() => ({}));
			if (!contribRes.ok) return String(contrib.error ?? 'Could not book the contribution.');
			await invalidate('app:account');
			// The contribution is recorded; the `sei` block says whether its ACR reached SEI.
			if (contrib?.sei?.status && contrib.sei.status !== 'instructed' && contrib.sei.detail) {
				toast.warning(`Recorded, but not lodged at SEI: ${contrib.sei.detail}`);
			} else {
				toast.success('Contribution recorded');
			}
			return null;
		}
		const response = await fetch(`/accounts/${a.uid}/instructions`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		});
		const result = await response.json().catch(() => ({}));
		if (!response.ok) return String(result.error ?? 'Could not raise the instruction.');
		await invalidate('app:account');
		if (response.status === 202) toast.success('Sent for review');
		// BOOKED BUT NOT SENT: the API keeps the row when the custodian refuses or cannot be
		// reached and says why in `provider_error`. Said here as a warning, not an error — the
		// instruction is on the record and its run is the retry — and not as plain success, or
		// the adviser assumes the money moved.
		else if (result.provider_error) {
			toast.warning(`Recorded, but not sent to the custodian: ${result.provider_error}`);
		}
		return null;
	}

	async function saveRecord(
		path: string,
		method: 'PUT' | 'PATCH',
		payload: object
	): Promise<string | null> {
		const response = await fetch(path, {
			method,
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(payload)
		});
		const body = await response.json().catch(() => ({}));
		if (!response.ok) return String(body.error ?? 'Could not save the change.');
		await invalidate('app:account');
		if (response.status === 202) toast.success('Sent for review');
		return null;
	}

	async function cancelReview(uid: string): Promise<string | null> {
		const response = await fetch(`/reviews/${uid}/cancel`, { method: 'POST' });
		const body = await response.json().catch(() => ({}));
		if (!response.ok) return String(body.error ?? 'Could not cancel the request.');
		await invalidate('app:account');
		toast.success('Request cancelled');
		return null;
	}

	// Categorising happens in the contributions workbench (the Action required strip links into
	// it), so this page keeps no local overlay of resolved items.
	const transactions = $derived(data.transactions);

	/** Ask Morgan Lloyd for a DCube SSO link.
	 *
	 * A FRESH one every time the drawer opens. The token is single-use: caching it and
	 * short-circuiting the refetch meant the first open worked and every reopen handed DCube a
	 * spent token, which it answers with "login failed" — indistinguishable from a broken
	 * integration. The server route already said "nothing is cached"; this is what made that true.
	 */
	async function openDrawdown() {
		drawdownOpen = true;
		if (drawdownLoading) return;
		drawdownUrl = null;
		drawdownLoading = true;
		drawdownError = null;
		try {
			const res = await fetch(`/accounts/${a.uid}/drawdown`, { method: 'POST' });
			const body = await res.json();
			if (res.ok && body.sso_url) drawdownUrl = body.sso_url;
			else drawdownError = body.error ?? 'Could not start a drawdown session.';
		} catch {
			drawdownError = 'Could not reach the drawdown service.';
		} finally {
			drawdownLoading = false;
		}
	}

	/**
	 * Push the client, their adviser, this account's scheme, its investment and its
	 * beneficiaries to Morgan Lloyd.
	 *
	 * The REPORT is the point, not a toast. Several records are created and each can fail on its
	 * own — the vendor refuses a contact for a missing detail, a scheme for an unconfigured
	 * pairing, a nomination for a missing relationship — so a single "done" would claim more than
	 * happened. `skipped` is a success here and says so: it means the identifier already existed,
	 * which is what makes pressing twice safe rather than duplicating a person in a real
	 * back-office system.
	 */
	async function pushToMorganLloyd() {
		pushOpen = true;
		if (pushBusy) return;
		pushBusy = true;
		pushError = null;
		pushReport = null;
		try {
			const res = await fetch(`/accounts/${a.uid}/push-to-morgan-lloyd`, { method: 'POST' });
			const body = await res.json();
			if (res.ok) {
				pushReport = body as MorganLloydPush;
				// The record now holds links it did not before, and the drawdown gate reads them.
				await invalidateAll();
			} else {
				pushError = body.error ?? 'Could not push this account to Morgan Lloyd.';
			}
		} catch {
			pushError = 'Could not reach Morgan Lloyd.';
		} finally {
			pushBusy = false;
		}
	}
</script>

<!-- THE PAGE FRAME cancels the shell's own padding and re-applies it, exactly as the clients and
     transactions listings do.

     IT ONLY OWNS THE VIEWPORT HEIGHT FOR A BAND THAT HOLDS A TABLE. Holdings and Transactions are
     one long table each, so the frame takes the screen (less the topbar), the header card keeps its
     natural height and the table takes the rest and scrolls its own rows — the money actions and
     the Action-required strip stay put however long the ledger is. `md:overflow-hidden` is what
     stops the page scrolling from the desktop breakpoint; `min-h-0` on every child down the chain
     is what lets the innermost table shrink instead of growing the column. On a phone the PAGE
     scrolls either way, so a table is never trapped in a nested pane.

     OVERVIEW IS NOT ONE OF THEM ANY MORE. It is a two-column record page now, the same shape the
     client record has, and a record page is read by scrolling it — trapping a rail and a stack of
     cards in a viewport-tall pane gives the reader two scrollbars and a band that clips whichever
     column is longer. -->
<div
	class={cn(
		'-m-4 flex min-w-0 flex-col gap-4 p-4 md:-m-6 md:p-6',
		locksViewport && 'md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden'
	)}
>
	{#if data.loadError}
		<!-- The holdings and the transactions come from two different stores (the lake and
		     `platform.transactions`), and either can be unreachable while the account record itself
		     loads fine. An empty Holdings or Transactions band then reads as "this account is empty",
		     which is the one wrong answer worth a banner. Inside the frame and `shrink-0`, so it
		     takes its height off the table rather than pushing the page past one screen. -->
		<div class="shrink-0">
			<LoadErrorBanner
				message="Some of this account’s holdings or transactions couldn’t be loaded."
				onRetry={() => invalidate('app:account')}
			/>
		</div>
	{/if}

	<AccountDetail
		account={data.account}
		holdings={data.holdings ?? []}
		allocations={data.allocations ?? []}
		transactions={transactions ?? []}
		{links}
		{section}
		canDrawdown={data.canDrawdown}
		canContribute={data.canContribute}
		instructions={data.instructions ?? []}
		contributionSummary={data.contributionSummary ?? null}
		transactionFilter={data.transactionFilter}
		transactionListing={data.transactionQuery}
		transactionTotal={data.transactionTotal}
		{transactionPageHref}
		transactionFacets={data.transactionFacets}
		{transactionExportHref}
		{holdingsExportHref}
		{ontransactionquery}
		oninstruction={data.canMoveMoney ? openInstruction : undefined}
		disabledActions={data.disabledActions ?? []}
		onchangemodel={data.canMoveMoney ? openModel : undefined}
		onSaveBank={data.canMoveMoney && data.account.bank_details_allowed !== false
			? (payload) => saveRecord(`/accounts/${a.uid}/bank`, 'PUT', payload)
			: undefined}
		onCancelReview={cancelReview}
		justificationRequired={data.justificationRequired ?? []}
		pendingReviews={data.pendingReviews ?? []}
		onSaveFees={data.canEditFees
			? (ids, justification) =>
					saveRecord(`/accounts/${a.uid}/fee-package`, 'PUT', {
						fee_package_ids: ids,
						...(justification ? { justification } : {})
					})
			: undefined}
		onSaveMovements={data.canMoveMoney
			? (payload) => saveRecord(`/accounts/${a.uid}/regular-movements`, 'PUT', payload)
			: undefined}
		feePackages={data.feePackages ?? []}
		onschedule={data.canMoveMoney ? (kind) => (instructionKind = kind) : undefined}
		ontransfer={data.canMoveMoney ? () => (transferOpen = true) : undefined}
		ondrawdown={data.canDrawdown ? openDrawdown : undefined}
		onpushtoadministrator={pushToMorganLloyd}
		canPushToAdministrator={data.canPushToMorganLloyd}
		beneficiaries={data.beneficiaries ?? []}
		beneficiaryActions={data.canEditContact
			? { enhance, add: '?/addBeneficiary', remove: '?/removeBeneficiary' }
			: undefined}
	>
		{#snippet actionRequired(items)}
			<ActionRequired {items} showClient={false} />
		{/snippet}
	</AccountDetail>
</div>

<ModelSheet
	bind:open={modelOpen}
	{models}
	modelId={a.model_id}
	modelName={a.model_name}
	justificationRequired={Boolean(
		(data.justificationRequired ?? []).includes('account.model_changed')
	)}
	submit={(body) => saveRecord(`/accounts/${a.uid}/model`, 'PUT', body)}
/>

<InstructionSheet
	bind:kind={instructionKind}
	accountId={a.id}
	accountUid={a.uid}
	platformIdentifier={a.platform_identifier}
	custodianAccountNumber={a.custodian_account_number}
	payInAccountName={a.pay_in_account_name}
	payInAccountSortCode={a.pay_in_sort_code}
	payInAccountNumber={a.pay_in_account_number}
	currency={a.currency}
	accountValue={a.value}
	cash={a.cash}
	bankAccounts={a.bank_accounts}
	clientBank={a.client.bank_account}
	instructions={data.instructions ?? []}
	withdrawalFees={a.withdrawal_fees}
	custodian={a.custodian}
	feePackages={data.feePackages ?? []}
	isPension={a.is_pension ?? isPensionProduct(a.product_type)}
	justificationRequired={moneyNeedsReason(data.justificationRequired ?? [])}
	bankReviewRequired={Boolean(
		(data.justificationRequired ?? []).includes('account.bank_details_changed')
	)}
	onSaveBank={data.canMoveMoney && data.account.bank_details_allowed !== false
		? (payload) => saveRecord(`/accounts/${a.uid}/bank`, 'PUT', payload)
		: undefined}
	submit={saveInstruction}
/>

<!-- Transfer in. Cash vs in-specie is always asked (never defaulted — cash liquidates). Full vs
     partial and the pension facts appear from the receiving account. Raising money movement
     takes `instructions:write`, so an adviser without it gets no trigger at all. -->
<TransferForm
	bind:open={transferOpen}
	account={{
		id: a.id,
		uid: a.uid,
		name: title,
		currency: a.currency,
		custodian: a.custodian,
		scheme_administrator: a.scheme_administrator,
		is_pension: a.is_pension ?? isPensionProduct(a.product_type)
	}}
	client={owner}
	clientAddress={a.client.address}
	onraised={() => invalidate('app:account')}
/>

<!-- The sync to Morgan Lloyd, and its REPORT.
     A panel rather than a toast because several records are created and each can fail on its own:
     the adviser, the contact, the `MLSIPP/…` scheme every scoped action is addressed by, the
     investment (policy) a valuation is filed against, and each beneficiary nomination. A single
     "done" would claim more than happened, and the case that matters most is the partial one. -->
<Sheet.Root
	bind:open={pushOpen}
	onOpenChange={(next) => {
		if (!next) {
			pushReport = null;
			pushError = null;
		}
	}}
>
	<Sheet.Content class="flex w-full flex-col gap-0 p-0">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>Sync to Morgan Lloyd: {title}</Sheet.Title>
			<Sheet.Description>
				{owner ? `${owner} · ` : ''}Creates the adviser, the client, the scheme, its investment and
				the beneficiaries at the scheme administrator.
			</Sheet.Description>
		</Sheet.Header>
		<div class="min-h-0 flex-1 overflow-auto">
			{#if pushBusy}
				<p class="text-muted-foreground p-6 text-sm">Creating records at Morgan Lloyd…</p>
			{:else if pushError}
				<div class="space-y-3 p-6">
					<p class="text-destructive text-sm">{pushError}</p>
					<Button variant="outline" size="sm" onclick={pushToMorganLloyd}>Try again</Button>
				</div>
			{:else if pushReport}
				{@const step = pushReport.accounts[0]}
				<dl class="space-y-4 p-6 text-sm">
					<div class="flex items-baseline justify-between gap-4">
						<dt class="text-muted-foreground">Adviser</dt>
						<dd class="text-right">
							{#if pushReport.adviser.status === 'failed'}
								<span class="text-destructive">{pushReport.adviser.error ?? 'Failed'}</span>
							{:else if pushReport.adviser.external_id}
								{pushReport.adviser.status === 'created' ? 'Created' : 'Already there'}
								<span class="text-muted-foreground block font-mono text-xs">
									{pushReport.adviser.external_id}
								</span>
							{:else}
								<span class="text-muted-foreground">Not on the book</span>
							{/if}
						</dd>
					</div>
					<div class="flex items-baseline justify-between gap-4">
						<dt class="text-muted-foreground">Client</dt>
						<dd class="text-right">
							{pushReport.contact.status === 'created' ? 'Created' : 'Already there'}
							{#if pushReport.contact.external_id}
								<span class="text-muted-foreground block font-mono text-xs">
									{pushReport.contact.external_id}
								</span>
							{/if}
						</dd>
					</div>
					{#if step}
						<div class="flex items-baseline justify-between gap-4">
							<dt class="text-muted-foreground">Scheme</dt>
							<dd class="text-right">
								{#if step.status === 'failed'}
									<span class="text-destructive">{step.error ?? 'Failed'}</span>
								{:else}
									{step.status === 'created' ? 'Created' : 'Already there'}
									{#if step.scheme_reference}
										<span class="text-muted-foreground block font-mono text-xs">
											{step.scheme_reference}
										</span>
									{/if}
								{/if}
							</dd>
						</div>
						<div class="flex items-baseline justify-between gap-4">
							<!-- The vendor calls it Create Investment; the record it mints is the policy. -->
							<dt class="text-muted-foreground">Investment</dt>
							<dd class="text-right">
								{#if step.policy_status === 'failed'}
									<span class="text-destructive">{step.policy_error ?? 'Failed'}</span>
								{:else if step.policy_status}
									{step.policy_status === 'created' ? 'Created' : 'Already there'}
									{#if step.policy_reference}
										<span class="text-muted-foreground block font-mono text-xs">
											{step.policy_reference}
										</span>
									{/if}
								{:else}
									<span class="text-muted-foreground">Not reached</span>
								{/if}
							</dd>
						</div>
						<div class="flex items-baseline justify-between gap-4">
							<dt class="text-muted-foreground">Beneficiaries</dt>
							<dd class="text-right">
								{#if !step.beneficiaries?.length}
									<span class="text-muted-foreground">None nominated</span>
								{:else}
									{@const failed = step.beneficiaries.filter((b) => b.status === 'failed')}
									{@const created = step.beneficiaries.filter((b) => b.status === 'created').length}
									{@const skipped = step.beneficiaries.filter((b) => b.status === 'skipped').length}
									{#if failed.length}
										<span class="text-destructive">
											{failed.length === 1
												? (failed[0].error ?? 'Failed')
												: `${failed.length} could not be synced`}
										</span>
									{:else if created}
										{created === 1 ? 'Created' : `${created} created`}
									{:else}
										Already there
									{/if}
									{#if skipped && created}
										<span class="text-muted-foreground block text-xs">
											{skipped} already there
										</span>
									{/if}
								{/if}
							</dd>
						</div>
					{/if}
				</dl>
				{#if pushReport.warnings.length}
					<div class="border-t px-6 py-4">
						{#each pushReport.warnings as warning (warning)}
							<p class="text-muted-foreground text-sm">{warning}</p>
						{/each}
					</div>
				{/if}
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>

<!-- Drawdown runs in CTC's DCube journey. Morgan Lloyd mints a one-time SSO link; we frame it
     here rather than punting the adviser to another tab, so they keep the account context. -->
<Sheet.Root
	bind:open={drawdownOpen}
	onOpenChange={(next) => {
		// Drop the spent token on close so the next open cannot reuse it.
		if (!next) {
			drawdownUrl = null;
			drawdownError = null;
		}
	}}
>
	<!-- FORM DRAWER (`sm:max-w-xl`). A hand-off panel, not a host for the journey — the
	     primitive's 58vw illustration width is far too wide for a title and one button. -->
	<Sheet.Content class="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
		<Sheet.Header class="border-b px-6 pt-6 pb-4">
			<Sheet.Title>Drawdown: {title}</Sheet.Title>
			<Sheet.Description>
				{owner ? `${owner} · ` : ''}Start this client’s drawdown journey with Morgan Lloyd.
			</Sheet.Description>
		</Sheet.Header>
		<div class="min-h-0 flex-1">
			{#if drawdownLoading}
				<p class="text-muted-foreground p-6 text-sm">Starting a secure session…</p>
			{:else if drawdownError}
				<div class="space-y-3 p-6">
					<p class="text-destructive text-sm">{drawdownError}</p>
					<Button
						variant="outline"
						size="sm"
						onclick={() => {
							drawdownUrl = null;
							openDrawdown();
						}}
					>
						Try again
					</Button>
				</div>
			{:else if drawdownUrl}
				<!-- Launched, not framed. CTC sends `frame-ancestors` and X-Frame-Options on
				     digitaltest.ctc.uk.com, so an iframe here renders a CSP error the adviser
				     reads as our bug. The session is theirs to host; ours is to hand it over
				     with the client's context intact. -->
				<div class="space-y-4 p-6">
					<p class="text-sm">The drawdown journey is ready. Open it in a new tab to continue.</p>
					<Button href={drawdownUrl} target="_blank" rel="noreferrer">
						<ExternalLinkIcon class="mr-1.5 size-4" /> Open the drawdown journey
					</Button>
					<p class="text-muted-foreground text-xs">
						This link is single-use. Close and reopen this panel to start another session.
					</p>
				</div>
			{/if}
		</div>
	</Sheet.Content>
</Sheet.Root>
