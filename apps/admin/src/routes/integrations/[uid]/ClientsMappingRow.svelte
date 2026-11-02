<script lang="ts">
	// One client row on the Clients tab: the client is fixed (this row IS that crm.contacts record).
	// It is laid out like an account row on the Accounts tab:
	//
	//  * Not linked: a dropdown of the client's Intelliflo candidates, and Link.
	//  * Linked: the Intelliflo client is shown, with Unlink and nothing else. To point the client at
	//    a different Intelliflo client it is unlinked first, then linked again.
	//
	// Only the client-level link is written here; the client's accounts are mapped on the Accounts
	// tab. Unlink asks first. WHILE ANY OF THE CLIENT'S ACCOUNTS IS STILL MAPPED it does not ask: it
	// says to unlink the accounts first, because those account links point at plans of THIS
	// Intelliflo client and would be left with nothing to resolve against. (The API enforces the
	// same rule, so a stale row cannot get round it.)
	import { untrack } from 'svelte';
	import { page } from '$app/state';
	import { Button, Combobox, ConfirmDialog, toast, type ComboboxOption } from '@graphene/ui';
	import type { ClientsTableRow, IoClientSearchResult, IoClientUnlinkResult } from '$lib/api';
	import { responseMessage } from './mapping-errors';

	let { row, canWrite }: { row: ClientsTableRow; canWrite: boolean } = $props();

	// Read once, on purpose: the parent keys `{#each}` on `contact_uid`, so this row is that client
	// for its whole life and a different client gets a fresh component, never a prop update.
	//
	// The row is read once per mount, so it is only as fresh as the Clients tab's last mount: the
	// page mounts that tab only while it is showing, so every visit starts from the current counts.
	const { contactUid, base, accountCount, initialSaved } = untrack(() => ({
		contactUid: row.contact_uid,
		base: `/integrations/${page.params.uid}`,
		accountCount: row.linked_account_count,
		initialSaved: row.io_client
	}));
	/** The Intelliflo client this client is linked to, or null when it is not linked. */
	let saved = $state<IoClientSearchResult | null>(initialSaved);

	/** The pick in the dropdown while the client is not linked. */
	let clientIdIo = $state('');
	let ioClients = $state<IoClientSearchResult[]>([]);
	let loading = $state(false);
	let linking = $state(false);
	let loadError = $state('');
	let confirmOpen = $state(false);
	/** Set when Unlink is pressed while accounts are still mapped: the row then says to unlink them. */
	let unlinkBlocked = $state(false);

	// The live candidates for this client, searched when the dropdown is first OPENED, not when the
	// row appears: a page of rows would otherwise fire one Intelliflo search each, all at once.
	let seq = 0;
	function loadCandidates() {
		const mine = ++seq;
		loading = true;
		loadError = '';
		fetch(`${base}/io-clients-for-contact?${new URLSearchParams({ contact_uid: contactUid })}`)
			.then(async (res) => {
				if (!res.ok)
					throw new Error(await responseMessage(res, 'Could not load Intelliflo clients'));
				const fetched = (await res.json()) as IoClientSearchResult[];
				if (mine !== seq) return;
				ioClients = fetched;
			})
			.catch((err: unknown) => {
				if (mine === seq) loadError = err instanceof Error ? err.message : 'Something went wrong.';
			})
			.finally(() => {
				if (mine === seq) loading = false;
			});
	}

	const options = $derived<ComboboxOption[]>(
		ioClients.map((c) => ({ value: c.client_id_io, label: c.display_name ?? c.client_id_io }))
	);

	async function link() {
		linking = true;
		try {
			const res = await fetch(`${base}/clients-link`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ contact_uid: contactUid, client_id_io: clientIdIo })
			});
			if (!res.ok) throw new Error(await responseMessage(res, 'Link failed'));
			saved = ioClients.find((c) => c.client_id_io === clientIdIo) ?? null;
			unlinkBlocked = false;
			toast.success('Client linked to Intelliflo Office.');
		} catch (err) {
			toast.error(err instanceof Error ? err.message : 'Could not link to Intelliflo Office.');
		} finally {
			linking = false;
		}
	}

	function startUnlink() {
		if (accountCount > 0) {
			unlinkBlocked = true;
			return;
		}
		confirmOpen = true;
	}

	async function unlink() {
		// Caught here: a rejection would escape `ConfirmDialog` as an unhandled rejection and leave
		// the dialog open with no message.
		try {
			const res = await fetch(`${base}/clients/${encodeURIComponent(contactUid)}/unlink`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: '{}'
			});
			if (!res.ok) {
				// The API refuses (409) when an account is still mapped, naming which ones.
				toast.error(await responseMessage(res, 'Unlink failed'));
				return;
			}
			const out = (await res.json()) as IoClientUnlinkResult;
			toast.success(
				out.unlinked
					? 'Client unlinked from Intelliflo Office.'
					: 'This client was already unlinked.'
			);
			saved = null;
			clientIdIo = '';
			ioClients = [];
			unlinkBlocked = false;
		} catch (err) {
			toast.error(err instanceof Error ? err.message : 'Could not unlink from Intelliflo Office.');
		}
	}
</script>

<div class="flex flex-col gap-2 lg:flex-row lg:items-start">
	<div class="min-w-0 flex-1">
		<p class="truncate text-sm font-medium">{row.contact_display_name}</p>
		{#if row.contact_tenant_name}
			<p class="text-muted-foreground truncate text-xs">{row.contact_tenant_name}</p>
		{/if}
		{#if accountCount > 0}
			<p class="text-muted-foreground text-xs">
				{accountCount} linked {accountCount === 1 ? 'account' : 'accounts'}
			</p>
		{/if}
	</div>
	<div class="min-w-0 flex-1">
		{#if saved}
			<p class="truncate text-sm">{saved.display_name ?? saved.client_id_io}</p>
		{:else if canWrite}
			<Combobox
				{options}
				bind:value={clientIdIo}
				placeholder="IO client…"
				emptyText={loading ? 'Loading…' : 'No matching Intelliflo clients.'}
				onOpen={loadCandidates}
			/>
		{:else}
			<p class="text-muted-foreground text-sm">Not linked</p>
		{/if}
	</div>
	<!-- `w-20` matches the header's button placeholder (ClientsMapping.svelte): a content-sized button
	     (Link is narrower than Unlink) would resize the two flex columns row by row, and the
	     dropdown would not line up with the rows around it. -->
	{#if canWrite}
		{#if saved}
			<Button type="button" variant="outline" class="w-20 shrink-0" onclick={startUnlink}>
				Unlink
			</Button>
		{:else}
			<Button type="button" class="w-20 shrink-0" disabled={!clientIdIo || linking} onclick={link}>
				{linking ? 'Linking…' : 'Link'}
			</Button>
		{/if}
	{/if}
</div>

{#if unlinkBlocked && accountCount > 0}
	<p class="text-warning-strong mt-1 text-sm">
		Account mapping exists ({accountCount}
		{accountCount === 1 ? 'account' : 'accounts'}). Unlink the accounts before unlinking the client.
	</p>
{/if}
{#if loadError}
	<p class="text-destructive mt-1 text-sm">{loadError}</p>
{/if}

<ConfirmDialog
	bind:open={confirmOpen}
	title="Unlink this client from Intelliflo?"
	description={`${row.contact_display_name} will no longer be linked to its Intelliflo client. It can be linked again afterwards.`}
	confirmLabel="Unlink"
	pendingLabel="Unlinking…"
	destructive
	onconfirm={unlink}
/>
