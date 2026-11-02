<script lang="ts">
	// This client's document. The same shared record the library's `/documents/[uid]` renders — only
	// the links and the archive destination differ, so "back" lands on the client's Documents
	// section rather than the firm-wide library.
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import PageTitle from '$lib/components/page-title.svelte';
	import DocumentRecord from '$lib/components/documents/document-record.svelte';
	import type { DocumentDetailLinks } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const clientUid = $derived(page.params.uid ?? '');
	const uid = $derived(page.params.doc ?? '');

	// `documents` is where the record's Back goes. Templates are firm-wide records with no per-client
	// view, so they stay on their own route.
	const links: DocumentDetailLinks = $derived({
		documents: `/clients/${clientUid}/documents`,
		template: (u) => `/templates/${u}`,
		// The client this document is filed for — one click from the record to their book.
		client: (uid: string) => `/clients/${uid}`
	});
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(record)}
		<PageTitle title="{record.doc?.name ?? 'Document'} · {data.clientName}" />
		<DocumentRecord
			data={{ ...data, ...record }}
			actionBase="/clients/{clientUid}/documents/{uid}"
			{links}
			archivedHref="/clients/{clientUid}/documents"
		/>
	{/snippet}
</Streamed>
