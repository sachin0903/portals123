<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { Alert, AlertDescription, Streamed, toast } from '@graphene/ui';
	import {
		ContributionsSkeleton,
		ContributionsWorkbench,
		type ContributionLinks
	} from '@graphene/ui/book/contributions';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { operateBase } from '$lib/record-focus';
	import type { ActionResult } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(operateBase(data.tenant.uid));
	const links: ContributionLinks = $derived({
		client: (uid: string) => `${base}/clients/${uid}`,
		account: (uid: string) => `${base}/accounts/${uid}`
	});
	let busy = $state(false);
	let formEl = $state<HTMLFormElement | null>(null);
	let action = $state('?/classify');
	let payload = $state<Record<string, string>>({});

	const href = (number: number) => pageHref(data.query, number, page.url.searchParams);
	function navigate(next: ListingQuery) {
		const query = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(query ? `?${query}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
	function submit(name: string, fields: Record<string, unknown> = {}) {
		if (!data.writing) return;
		action = name;
		payload = Object.fromEntries(
			Object.entries(fields)
				.filter(([, value]) => value != null)
				.map(([key, value]) => [key, String(value)])
		);
		queueMicrotask(() => formEl?.requestSubmit());
	}
	const patchFields = (patch: Record<string, unknown>) => {
		const employer = (patch.employer ?? {}) as Record<string, unknown>;
		return {
			category: patch.category,
			frequency: patch.frequency,
			paid_by: patch.paid_by,
			tax_year: patch.tax_year,
			employer_name: employer.employer_name,
			address_line1: employer.address_line1,
			city: employer.city,
			postcode: employer.postcode,
			email: employer.email
		};
	};
	const actions = $derived(
		data.writing
			? {
					classify: (uid: string, patch: Record<string, unknown>) =>
						submit('?/classify', { uid, ...patchFields(patch) }),
					classifyCredit: (transactionId: number, patch: Record<string, unknown>) =>
						submit('?/classifyCredit', {
							transaction_id: transactionId,
							...patchFields(patch)
						}),
					record: (uid: string) => submit('?/record', { uid }),
					lodge: (uid: string) => submit('?/lodge', { uid }),
					cancel: (uid: string, reason: string) => submit('?/cancel', { uid, reason }),
					refreshFromMorganLloyd: (uid: string) => submit('?/refresh', { uid }),
					runMatcher: () => submit('?/match')
				}
			: undefined
	);
	const errorOf = (result: ActionResult) =>
		result.type === 'failure'
			? String(result.data?.error ?? 'Something went wrong.')
			: 'Something went wrong.';
</script>

<svelte:head><title>Contributions · {data.tenant.name}</title></svelte:head>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<ContributionsSkeleton />
	{/snippet}
	{#snippet children(book)}
		{#if book.creditsFailed}
			<Alert variant="destructive" class="mb-4">
				<AlertDescription>
					The unclassified-credit queue could not be loaded, so this workbench is incomplete.
				</AlertDescription>
			</Alert>
		{/if}
		<ContributionsWorkbench
			contributions={book.contributions}
			credits={book.credits}
			summary={book.summary}
			focus={data.focus}
			listing={data.query}
			matchCount={book.matchCount}
			facets={book.facets}
			onquery={navigate}
			pageHref={href}
			{links}
			{busy}
			{actions}
		/>
	{/snippet}
</Streamed>

{#if data.writing}
	<form
		bind:this={formEl}
		method="POST"
		{action}
		class="hidden"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				busy = false;
				if (result.type === 'success') toast.success('Saved.');
				else toast.error(errorOf(result));
				await update({ reset: false });
			};
		}}
	>
		{#each Object.entries(payload) as [name, value] (name)}
			<input type="hidden" {name} {value} />
		{/each}
	</form>
{/if}
