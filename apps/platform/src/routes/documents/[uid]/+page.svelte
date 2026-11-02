<script lang="ts">
	// The library's document detail — a thin mount of the shared record, which is also hosted at
	// `/clients/[id]/documents/[doc]`. Everything that differs between the two hosts is a prop.
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { RecordSkeleton, Streamed } from '@graphene/ui';
	import PageTitle from '$lib/components/page-title.svelte';
	import DocumentRecord from '$lib/components/documents/document-record.svelte';
	import type { DocumentDetailLinks } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	const id = $derived(page.params.uid ?? '');

	const links: DocumentDetailLinks = {
		documents: '/documents',
		template: (uid) => `/templates/${uid}`,
		// The client this document is filed for — one click from the record to their book.
		client: (uid: string) => `/clients/${uid}`
	};
</script>

<Streamed data={data.record} onretry={() => invalidateAll()}>
	{#snippet pending()}
		<RecordSkeleton cards={2} />
	{/snippet}
	{#snippet children(record)}
		<PageTitle title="{record.doc?.name ?? 'Document'} · Platform" />
		<DocumentRecord
			data={{ ...data, ...record }}
			actionBase="/documents/{id}"
			{links}
			archivedHref="/documents"
		/>
	{/snippet}
</Streamed>
