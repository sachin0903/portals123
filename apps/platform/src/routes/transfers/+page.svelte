<script lang="ts">
	// The transfers workbench — a thin mount.
	//
	// The listing is SERVER-DRIVEN: search, filters and the page live in the URL, the loader hands
	// them to the API, and the component only asks for changes. This page owns the navigation
	// because `$app/navigation` is SvelteKit and `@graphene/ui` is not.
	import { enhance } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { tick } from 'svelte';
	import { page } from '$app/state';
	import { LoadErrorBanner, Streamed, toast } from '@graphene/ui';
	import { TransfersSkeleton, TransfersWorkbench } from '@graphene/ui/book/transfers';
	import { pageHref, paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import type { ActionResult } from '@sveltejs/kit';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	let busy = $state(false);
	let stepEl = $state<HTMLFormElement | null>(null);
	let noteEl = $state<HTMLFormElement | null>(null);
	let stepUid = $state('');
	let stepId = $state('');
	let noteUid = $state('');
	let noteText = $state('');

	function errorOf(result: ActionResult): string {
		return result.type === 'failure'
			? String(result.data?.message ?? 'Something went wrong.')
			: 'Something went wrong.';
	}

	/** Real links for the pager. The current params are passed so an unrelated one survives. */
	const href = (n: number) => pageHref(data.query, n, page.url.searchParams);

	/** `keepFocus` so typing in the search box is not interrupted by the reload it triggers;
	 *  `noScroll` so paging does not jump the viewport to the top of the page. */
	function navigate(next: ListingQuery) {
		const params = paramsFromQuery(next, page.url.searchParams);
		const qs = params.toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
</script>

<Streamed data={data.book} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<TransfersSkeleton />
	{/snippet}
	{#snippet children(book)}
		{#if book.forbidden}
			<LoadErrorBanner message="You do not have access to transfers." />
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
			links={{ client: (uid) => `/clients/${uid}`, account: (uid) => `/accounts/${uid}` }}
			{busy}
			onnote={async (uid, note) => {
				noteUid = uid;
				noteText = note;
				await tick();
				noteEl?.requestSubmit();
			}}
			onstep={async (uid, step) => {
				stepUid = uid;
				stepId = step;
				await tick();
				stepEl?.requestSubmit();
			}}
		/>
	{/snippet}
</Streamed>

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
			else toast.error(errorOf(result));
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
			if (result.type === 'success') {
				toast.success(String(result.data?.message ?? 'Done.'));
			} else {
				toast.error(errorOf(result));
			}
			await update({ reset: false });
		};
	}}
>
	<input type="hidden" name="uid" value={stepUid} />
	<input type="hidden" name="step" value={stepId} />
</form>
