<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import {
		AccountDetail,
		Button,
		Dialog,
		InstructionSheet,
		Label,
		LoadErrorBanner,
		SelectMenu,
		Textarea,
		asRegularMovements,
		isPensionProduct,
		type AccountDetailData,
		type AccountBookLinks,
		type AccountSection,
		type AccountSheetKind,
		type FeePackageOption,
		type Holding,
		type InstructionRow,
		type SheetSubmitBody,
		type Transaction
	} from '@graphene/ui';
	import { moneyNeedsReason } from '@graphene/ui/book/reviews';
	import { TransferForm } from '@graphene/ui/book/transfers';

	let {
		data,
		section,
		transactions = []
	}: {
		data: {
			tenant: { uid: string };
			account: AccountDetailData;
			holdings: Holding[];
			instructions: InstructionRow[];
			// The catalogue row whole: the fees editor states what each package charges and the ad
			// hoc fee drawer groups it by category. A narrowed type silently drops both.
			feePackages: FeePackageOption[];
			models: Array<{ id: number; name: string }>;
			writing: boolean;
			canDrawdown: boolean;
			canContribute: boolean;
			loadError: boolean;
			title: string;
			owner: string | null;
			justificationRequired?: string[];
		};
		section: AccountSection;
		transactions?: Transaction[];
	} = $props();

	const base = $derived(`/tenants/${data.tenant.uid}/operate/accounts/${data.account.uid}`);
	const links: AccountBookLinks = $derived({
		account: (uid) => `/tenants/${data.tenant.uid}/operate/accounts/${uid}`,
		accountTransactions: (uid) =>
			`/tenants/${data.tenant.uid}/operate/accounts/${uid}/transactions`,
		client: (uid) => `/tenants/${data.tenant.uid}/operate/clients/${uid}`
	});
	let transferOpen = $state(false);
	let instructionKind = $state<AccountSheetKind | null>(null);
	// No 'address': the account record does not carry the client's address (it lives on the
	// client record, which is where it is edited), so nothing can open that editor.
	let editor = $state<'model' | null>(null);
	let saving = $state(false);
	let actionError = $state<string | null>(null);

	async function send(path: string, method: string, body?: unknown) {
		const response = await fetch(path, {
			method,
			headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
			body: body === undefined ? undefined : JSON.stringify(body)
		});
		const result = await response.json().catch(() => ({}));
		if (!response.ok) actionError = result.error ?? 'The action could not be completed.';
		else {
			actionError = null;
			await invalidateAll();
		}
		return { response, result };
	}

	async function save(path: string, method: string, body: unknown): Promise<string | null> {
		const { response, result } = await send(path, method, body);
		if (response.ok) return null;
		return String(result.error ?? 'The action could not be completed.');
	}

	function openInstruction(kind: AccountSheetKind) {
		// A SIPP does not pay cash away from this record. Drawdown mints a DCube session,
		// and only when this scheme is Morgan Lloyd-administered (`canDrawdown`). A pension
		// that is not must not hit DCube and fail after the mint.
		if (kind === 'withdrawal' && data.canDrawdown) {
			void drawdown();
			return;
		}
		instructionKind = kind;
	}

	async function saveInstruction(body: SheetSubmitBody): Promise<string | null> {
		if (
			body.kind === 'regular_withdrawal' &&
			(data.account.is_pension ?? isPensionProduct(data.account.product_type))
		) {
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
			const payments = asRegularMovements(data.account.payments);
			const withdrawals = asRegularMovements(data.account.withdrawals);
			return save(`${base}/update/movements`, 'PUT', {
				payments: body.kind === 'regular_payment' ? [...payments, row] : payments,
				withdrawals: body.kind === 'regular_withdrawal' ? [...withdrawals, row] : withdrawals,
				...(body.justification ? { justification: body.justification } : {})
			});
		}
		const response = await fetch(`${base}/instructions`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(body)
		});
		const result = await response.json().catch(() => ({}));
		if (!response.ok) return String(result.error ?? 'The action could not be completed.');
		await invalidateAll();
		return null;
	}

	function openModelEditor() {
		actionError = null;
		editor = 'model';
	}

	async function drawdown() {
		const { response, result } = await send(`${base}/drawdown`, 'POST');
		if (response.ok && result.sso_url) window.open(result.sso_url, '_blank', 'noopener,noreferrer');
	}
</script>

{#if data.loadError}
	<LoadErrorBanner
		message="Some account data could not be loaded."
		onRetry={() => invalidateAll()}
	/>
{/if}

{#if data.writing}
	<div class="mb-3 flex justify-end">
		<Button variant="outline" size="sm" onclick={openModelEditor}>Amend model</Button>
	</div>
{/if}

<AccountDetail
	account={data.account}
	holdings={data.holdings}
	{transactions}
	{links}
	{section}
	canDrawdown={data.canDrawdown}
	canContribute={data.canContribute}
	instructions={data.instructions}
	oninstruction={data.writing ? openInstruction : undefined}
	justificationRequired={data.justificationRequired ?? []}
	onSaveBank={data.writing && data.account.bank_details_allowed !== false
		? (payload) => save(`${base}/update/bank`, 'PUT', payload)
		: undefined}
	onSaveFees={data.writing
		? (ids, justification) =>
				save(`${base}/update/fees`, 'PUT', {
					fee_package_ids: ids,
					...(justification ? { justification } : {})
				})
		: undefined}
	onSaveMovements={data.writing
		? (payload) => save(`${base}/update/movements`, 'PUT', payload)
		: undefined}
	feePackages={data.feePackages}
	onschedule={data.writing ? (kind) => (instructionKind = kind) : undefined}
	ontransfer={data.writing ? () => (transferOpen = true) : undefined}
	ondrawdown={data.canDrawdown ? drawdown : undefined}
/>

<TransferForm
	bind:open={transferOpen}
	account={{
		id: data.account.id,
		uid: data.account.uid,
		name: data.title,
		currency: data.account.currency,
		custodian: data.account.custodian,
		scheme_administrator: data.account.scheme_administrator,
		is_pension: data.account.is_pension ?? isPensionProduct(data.account.product_type)
	}}
	client={data.owner}
	clientAddress={data.account.client.address}
	endpoint={`${base}/transfers`}
/>

<InstructionSheet
	bind:kind={instructionKind}
	accountId={data.account.id}
	accountUid={data.account.uid}
	platformIdentifier={data.account.platform_identifier}
	currency={data.account.currency}
	accountValue={data.account.value}
	cash={data.account.cash}
	bankAccounts={data.account.bank_accounts}
	clientBank={data.account.client.bank_account}
	instructions={data.instructions}
	withdrawalFees={data.account.withdrawal_fees}
	custodian={data.account.custodian}
	feePackages={data.feePackages}
	isPension={data.account.is_pension ?? isPensionProduct(data.account.product_type)}
	onSaveBank={data.writing && data.account.bank_details_allowed !== false
		? (payload) => save(`${base}/update/bank`, 'PUT', payload)
		: undefined}
	justificationRequired={moneyNeedsReason(data.justificationRequired ?? [])}
	bankReviewRequired={(data.justificationRequired ?? []).includes('account.bank_details_changed')}
	submit={saveInstruction}
/>

{#if editor === 'model'}
	<Dialog.Root open onOpenChange={(open) => !open && (editor = null)}>
		<Dialog.Content class="sm:max-w-md">
			<Dialog.Title>Amend model</Dialog.Title>
			<form
				class="space-y-3"
				onsubmit={async (event) => {
					event.preventDefault();
					const form = new FormData(event.currentTarget);
					const value = String(form.get('model_id') ?? '');
					const justification = String(form.get('justification') ?? '').trim();
					saving = true;
					const result = await send(`${base}/update/model`, 'PUT', {
						model_id: value ? Number(value) : null,
						...(justification ? { justification } : {})
					});
					saving = false;
					if (result.response.ok) editor = null;
				}}
			>
				<SelectMenu
					name="model_id"
					value={data.account.model_id == null ? '' : String(data.account.model_id)}
					items={[
						{ value: '', label: 'No model' },
						...data.models.map((model) => ({ value: String(model.id), label: model.name }))
					]}
				/>
				{#if (data.justificationRequired ?? []).includes('account.model_changed')}
					<div class="space-y-1">
						<Label for="operate-model-justification">Why this change</Label>
						<Textarea id="operate-model-justification" name="justification" rows={2} required />
					</div>
				{/if}
				{#if actionError}<p class="text-destructive text-sm">{actionError}</p>{/if}
				<Button type="submit" disabled={saving}>Save model</Button>
			</form>
		</Dialog.Content>
	</Dialog.Root>
{/if}
