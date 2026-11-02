<script lang="ts">
	// Thin route over the shared documents library (@graphene/ui book/documents — the portal
	// waterfall: the admin portal renders the same component read-only over its own loaders).
	// What stays here is exactly what has a side effect or a platform route behind it: the
	// fetches to this route's form actions (archive/delete/retry), their
	// toasts + invalidateAll, and the upload dialog. Also used by /documents/create,
	// /documents/requests and the clients tab). The shared component gets typed rows, this portal's
	// link builders, and — because this user can write — the action callbacks.
	import { goto, invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import { paramsFromQuery, type ListingQuery } from '@graphene/ui/book/listing';
	import { Streamed, toast } from '@graphene/ui';
	import {
		DocumentsLibrary,
		DocumentsLibrarySkeleton,
		type DocumentsLibraryActions,
		type DocumentsLibraryLinks,
		type LibraryRow
	} from '@graphene/ui/book/documents';
	import UploadDialog from '$lib/components/documents/upload-dialog.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/** Push a changed search/filter into the URL, which re-runs the loader. `keepFocus` is what lets
	 *  the search box survive the reload it triggers; `replaceState` keeps a filter fiddle out of the
	 *  back history, so Back leaves the library rather than stepping through every keystroke. */
	function navigate(next: ListingQuery): void {
		const qs = paramsFromQuery(next, page.url.searchParams).toString();
		void goto(qs ? `?${qs}` : page.url.pathname, {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}

	const links: DocumentsLibraryLinks = {
		document: (uid) => `/documents/${uid}`,
		documentContent: (uid) => `/documents/${uid}/content`,
		fileDownload: (uid) => `/files/${uid}/download`,
		client: (uid) => `/clients/${uid}`,
		requests: '/documents/requests',
		templates: '/templates'
	};

	// The host dialogs (shared with /documents/create, /documents/requests and the clients tab).
	let uploadOpen = $state(false);
	let presetFiles = $state<File[] | null>(null);
	$effect(() => {
		if (page.url.searchParams.get('upload') === '1') uploadOpen = true;
	});
	$effect(() => {
		if (!uploadOpen) presetFiles = null;
	});

	/** Name the rows a bulk action couldn't touch — a bare count leaves the user hunting. The rows are
	 *  passed in because the library is streamed: they exist only once the promise has settled. */
	const nameList = (rows: LibraryRow[], uids: string[]): string => {
		const nameOf = (uid: string) => rows.find((r) => r.uid === uid)?.name ?? uid;
		return (
			uids.slice(0, 3).map(nameOf).join(', ') +
			(uids.length > 3 ? `, +${uids.length - 3} more` : '')
		);
	};

	/** POST one uid to a form action per document; returns the uids that failed. */
	async function postEach(action: string, uids: string[]): Promise<string[]> {
		const failed: string[] = [];
		for (const uid of uids) {
			const body = new FormData();
			body.append('uid', uid);
			const res = await fetch(action, { method: 'POST', body });
			if (!res.ok) failed.push(uid);
		}
		return failed;
	}

	// The library's injected mutations: each owns its fetch + toast + invalidateAll (the shared
	// component owns the selection/pending state around them). Built from the rows on screen, so a
	// failure report can name the documents it could not touch.
	const writeActions = (rows: LibraryRow[]): DocumentsLibraryActions => ({
		// Delete = the API's soft `archive` (the enum keeps its word; the product does not). No
		// restore counterpart here: the library has no deleted view, so there is nothing to restore
		// FROM — that lives on the document's own record.
		archive: async (uids) => {
			const failed = await postEach('/documents?/archive', uids);
			await invalidate('app:documents');
			if (failed.length === 0) {
				toast.success(
					uids.length === 1 ? 'Document deleted.' : `Deleted ${uids.length} documents.`
				);
			} else {
				toast.error(`Couldn't delete ${nameList(rows, failed)}.`);
			}
		},
		// Supporting-file deletes post to the /files route's action (kept live behind its redirect).
		deleteFiles: async (uids) => {
			const failed = await postEach('/files?/delete', uids);
			await invalidate('app:documents');
			if (failed.length === 0) {
				toast.success(uids.length === 1 ? 'File deleted.' : `Deleted ${uids.length} files.`);
			} else {
				toast.error(`Couldn't delete ${nameList(rows, failed)}.`);
			}
		},
		// Requeue a failed render. The row stays in place until the worker picks the job up again, so
		// the toast (and the refreshed status badge) is the whole feedback.
		retryRender: async (uid) => {
			const body = new FormData();
			body.append('uid', uid);
			const res = await fetch('/documents?/retryRender', { method: 'POST', body });
			if (!res.ok) {
				toast.error("Couldn't restart this document. Ask your administrator for help.");
				return;
			}
			await invalidate('app:documents');
			toast.success('Document preparation restarted.');
		},
		upload: (files) => {
			presetFiles = files ?? null;
			uploadOpen = true;
		}
	});
</script>

<svelte:head><title>Documents · Platform</title></svelte:head>

<!-- Streamed: the library is five parallel reads, so it was the surface a sidebar click waited
     longest on. The permissions above it are session-resolved, so the page's shape — and every gate
     on it — is on screen immediately. -->
<Streamed data={data.library} onretry={() => invalidate('app:documents')}>
	{#snippet pending()}
		<DocumentsLibrarySkeleton actions activity={data.canReadActivity} />
	{/snippet}
	{#snippet children(library)}
		<!-- Exactly the callbacks this caller's permissions earn: drafting on `documents:write`. Null ⇒
		     the library reads read-only. `documents:write` ALONE, and `documents:send` earns nothing
		     here: notifying is gone entirely (a document REQUEST asks a client to act and records
		     whether they did), so there is no notify affordance and no `?/notify` action for one to post
		     to. `documents:send` still gates raising a request, which lives on the record and on a
		     client's Documents tab, not in this library. -->
		<DocumentsLibrary
			documents={library.documents}
			activity={library.activity}
			canReadActivity={data.canReadActivity}
			loadError={library.loadError}
			listing={data.query}
			facets={library.facets}
			facetLabels={library.facetLabels}
			total={library.total}
			onquery={navigate}
			{links}
			actions={data.canManage ? writeActions(library.documents) : null}
			onretry={() => invalidate('app:documents')}
			onnavigate={(href) => goto(href)}
		/>

		<UploadDialog
			bind:open={uploadOpen}
			{presetFiles}
			clients={data.canManage ? library.clients : null}
			documentTypes={library.documentTypes}
		/>
	{/snippet}
</Streamed>
