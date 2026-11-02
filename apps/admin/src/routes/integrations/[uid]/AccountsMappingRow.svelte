<script lang="ts">
	// One ACCOUNT row on the Accounts tab. Its client is already mapped to an Intelliflo client (the
	// listing only contains such accounts), so the plans offered are that client's own.
	//
	//  * Not linked: pick one of the client's plans and Link.
	//  * Linked: the plan is shown, with Unlink. To move an account to another plan it is unlinked
	//    and linked again, so there is one way to change a mapping, and it is explicit.
	//
	// Unlink asks first, because it writes to Intelliflo: the plan's policy number is blanked there
	// (when this link put our number in it) and the mapping is removed. One account at a time.
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { Button, Combobox, ConfirmDialog, toast, type ComboboxOption } from '@graphene/ui';
	import type { AccountsTableRow, IoPlanSearchResult, IoUnlinkResult } from '$lib/api';
	import { responseMessage } from './mapping-errors';

	let {
		row,
		canWrite,
		loadPlans,
		forgetPlans
	}: {
		row: AccountsTableRow;
		canWrite: boolean;
		/** The client's plans, shared by every row of the same Intelliflo client (one lookup). */
		loadPlans: (clientIdIo: string) => Promise<IoPlanSearchResult[]>;
		/** Drop that cache entry: the plan's policy number changed in Intelliflo. */
		forgetPlans: (clientIdIo: string) => void;
	} = $props();

	// Read once, on purpose: the parent keys `{#each}` on `account_uid`, so this row is that account
	// for its whole life.
	const { accountUid, clientIdIo, base } = untrack(() => ({
		accountUid: row.account_uid,
		clientIdIo: row.client_id_io,
		base: `/integrations/${page.params.uid}`
	}));

	let plan = $state<IoPlanSearchResult | null>(untrack(() => row.plan));
	let planIdIo = $state('');
	let ioPlans = $state<IoPlanSearchResult[]>([]);
	let loadingPlans = $state(false);
	let loadError = $state('');
	let linking = $state(false);
	let confirmOpen = $state(false);

	// The plans are only needed while the account is NOT linked (and the admin can write), and are
	// fetched when the dropdown is first OPENED: a page of unlinked rows would otherwise cost one
	// Intelliflo call per client on load. A linked row shows its saved plan and costs none. After an
	// unlink the row returns to the dropdown, which loads them again when opened.
	let seq = 0;
	function loadOptions() {
		const mine = ++seq;
		loadingPlans = true;
		loadError = '';
		loadPlans(clientIdIo)
			.then((list) => {
				if (mine === seq) ioPlans = list;
			})
			.catch((err: unknown) => {
				if (mine === seq) loadError = err instanceof Error ? err.message : 'Something went wrong.';
			})
			.finally(() => {
				if (mine === seq) loadingPlans = false;
			});
	}

	// "reference - planType - portfolioCategory", whichever of the three the plan carries: the same
	// label the platform portal's IO Plans dropdown uses.
	function planLabel(p: IoPlanSearchResult): string {
		return (
			[p.reference, p.plan_type, p.portfolio_category]
				.filter((part): part is string => Boolean(part))
				.join(' - ') || p.plan_id_io
		);
	}
	const options = $derived<ComboboxOption[]>(
		ioPlans.map((p) => ({ value: p.plan_id_io, label: planLabel(p) }))
	);

	async function link() {
		if (!planIdIo) return;
		linking = true;
		try {
			const res = await fetch(`${base}/accounts-link`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ account_uid: accountUid, plan_id_io: planIdIo })
			});
			if (!res.ok) throw new Error(await responseMessage(res, 'Link failed'));
			plan = (await res.json()) as IoPlanSearchResult;
			planIdIo = '';
			// The link wrote our number into that plan in Intelliflo, so the cached list is stale:
			// a sibling row of this client would otherwise still offer the plan just taken, and be
			// refused for it.
			forgetPlans(clientIdIo);
			toast.success('Account linked to its Intelliflo plan.');
		} catch (err) {
			toast.error(err instanceof Error ? err.message : 'Could not link to Intelliflo Office.');
		} finally {
			linking = false;
		}
	}

	async function unlink() {
		// Caught here: a rejection would escape `ConfirmDialog` as an unhandled rejection and leave
		// the dialog open with no message.
		try {
			const res = await fetch(`${base}/accounts/${encodeURIComponent(accountUid)}/unlink`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: '{}'
			});
			if (!res.ok) {
				// The API keeps the link when Intelliflo fails, and its message says so.
				toast.error(await responseMessage(res, 'Unlink failed'));
				return;
			}
			const out = (await res.json()) as IoUnlinkResult;
			toast.success(
				out.policy_number_cleared
					? 'Unlinked. The plan’s policy number was cleared in Intelliflo.'
					: `Unlinked.${out.note ? ` ${out.note}` : ''}`
			);
			forgetPlans(clientIdIo);
			plan = null;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : 'Could not unlink from Intelliflo Office.');
		}
	}
</script>

<div class="flex flex-col gap-2 lg:flex-row lg:items-start">
	<div class="min-w-0 flex-1">
		<p class="truncate text-sm font-medium">{row.contact_display_name}</p>
	</div>
	<div class="min-w-0 flex-1">
		<p class="truncate text-sm">{row.account_display_name}</p>
	</div>
	<div class="min-w-0 flex-1">
		{#if plan}
			<p class="truncate text-sm">{planLabel(plan)}</p>
			{#if plan.policy_number}
				<p class="text-muted-foreground truncate text-xs">Policy number {plan.policy_number}</p>
			{/if}
		{:else if canWrite}
			<Combobox
				{options}
				bind:value={planIdIo}
				placeholder="IO plan…"
				emptyText={loadingPlans
					? 'Loading…'
					: 'No plans without a policy number for this Intelliflo client.'}
				onOpen={loadOptions}
			/>
		{:else}
			<p class="text-muted-foreground text-sm">Not linked</p>
		{/if}
	</div>
	{#if canWrite}
		{#if plan}
			<Button
				type="button"
				variant="outline"
				class="w-20 shrink-0"
				onclick={() => (confirmOpen = true)}
			>
				Unlink
			</Button>
		{:else}
			<Button type="button" class="w-20 shrink-0" disabled={!planIdIo || linking} onclick={link}>
				{linking ? 'Linking…' : 'Link'}
			</Button>
		{/if}
	{/if}
</div>

{#if loadError}
	<p class="text-destructive mt-1 text-sm">{loadError}</p>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title="Unlink this account?"
	description={`${row.account_display_name} will be unlinked from its Intelliflo plan. The plan's policy number is cleared in Intelliflo, and the account can then be linked to another plan.`}
	confirmLabel="Unlink"
	pendingLabel="Unlinking…"
	destructive
	onconfirm={unlink}
/>
