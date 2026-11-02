<script lang="ts">
	// Thin mount of the SHARED account record (@graphene/ui book/accounts), fully read-only: no
	// `ondrawdown` callbacks and no `actionRequired` snippet, so every trigger the
	// platform page wires stays hidden; `canDrawdown` is left false for the same reason — a
	// drawdown session mints a live DCube SSO journey, which is a firm action, not an ops read.
	// No `newTransfer` link builder either.
	import {
		AccountDetail,
		clientName,
		LoadErrorBanner,
		providerReference,
		type AccountBookLinks
	} from '@graphene/ui';
	import { invalidateAll } from '$app/navigation';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const a = $derived(data.account);
	const title = $derived(a.name ?? providerReference(a.uid) ?? 'Account');
	const owner = $derived(a.client.id == null ? null : clientName(a.client));
	const base = $derived(`/tenants/${data.tenant.uid}/book`);

	const links: AccountBookLinks = $derived({
		account: (uid) => `${base}/accounts/${uid}`,
		client: (uid) => `${base}/clients/${uid}`
	});
</script>

<svelte:head>
	<title>{title}{owner ? ` · ${owner}` : ''} · {data.tenant.name} · Operations</title>
</svelte:head>

{#if data.loadError}
	<!-- The holdings and the transactions come from two different stores, and either can be
	     unreachable while the account itself loads. A blank band would read as "this account holds
	     nothing", which is the one wrong answer worth interrupting an operator for. -->
	<div class="mb-4">
		<LoadErrorBanner
			message="Some of this account’s holdings or transactions couldn’t be loaded."
			onRetry={() => invalidateAll()}
		/>
	</div>
{/if}

<AccountDetail
	account={data.account}
	holdings={data.holdings}
	transactions={data.transactions}
	{links}
/>
