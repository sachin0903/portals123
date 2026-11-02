<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import { LoadErrorBanner, Streamed, toast } from '@graphene/ui';
	import { TransfersSkeleton, TransfersWorkbench } from '@graphene/ui/book/transfers';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { operateBase } from '$lib/record-focus';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(operateBase(data.tenant.uid));
	let busy = $state(false);
	let stepEl = $state<HTMLFormElement | null>(null);
	let noteEl = $state<HTMLFormElement | null>(null);
	let stepUid = $state('');
	let stepId = $state('');
	let noteUid = $state('');
	let noteText = $state('');
	const href = (number: number) => pageHref(data.query, number, page.url.searchParams);
	function navigate(next: ListingQuery) {
		const query = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(query ? `?${query}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<svelte:head><title>Transfers · {data.tenant.name}</title></svelte:head>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<TransfersSkeleton />
	{/snippet}
	{#snippet children(book)}
		{#if book.forbidden}
			<LoadErrorBanner message="You do not have access to this firm’s transfers." />
		{:else if book.loadError}
			<LoadErrorBanner message="Transfers couldn’t be loaded." onRetry={() => invalidateAll()} />
		{/if}
		<TransfersWorkbench
			transfers={book.transfers}
			summary={book.summary}
			total={book.total}
			facets={book.facets}
			custodians={book.custodians}
			query={data.query}
			onquery={navigate}
			pageHref={href}
			links={{
				client: (uid) => `${base}/clients/${uid}`,
				account: (uid) => `${base}/accounts/${uid}`
			}}
			{busy}
			onnote={data.writing
				? async (uid, note) => {
						noteUid = uid;
						noteText = note;
						await tick();
						noteEl?.requestSubmit();
					}
				: undefined}
			onstep={data.writing
				? async (uid, step) => {
						stepUid = uid;
						stepId = step;
						await tick();
						stepEl?.requestSubmit();
					}
				: undefined}
		/>
	{/snippet}
</Streamed>

{#if data.writing}
	<form
		bind:this={noteEl}
		method="POST"
		action="?/note"
		class="hidden"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				busy = false;
				if (result.type === 'success') toast.success('Note saved.');
				else {
					toast.error(
						String(result.type === 'failure' ? result.data?.error : 'Could not save the note.')
					);
				}
				await update({ reset: false });
			};
		}}
	>
		<input type="hidden" name="uid" value={noteUid} />
		<input type="hidden" name="note" value={noteText} />
	</form>
	<form
		bind:this={stepEl}
		method="POST"
		action="?/step"
		class="hidden"
		use:enhance={() => {
			busy = true;
			return async ({ result, update }) => {
				busy = false;
				if (result.type === 'success') toast.success(String(result.data?.message ?? 'Done.'));
				else
					toast.error(
						String(result.type === 'failure' ? result.data?.error : 'The provider call failed.')
					);
				await update({ reset: false });
			};
		}}
	>
		<input type="hidden" name="uid" value={stepUid} />
		<input type="hidden" name="step" value={stepId} />
	</form>
{/if}
