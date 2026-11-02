<script lang="ts">
	// Thin shell over the shared document detail (@graphene/ui book/documents — the portal
	// waterfall: the admin portal renders the same record read-only over its own loaders). What
	// stays here is exactly what has a side effect or a platform route behind it: the
	// deserialize-based POSTs to the hosting route's `?/rename` and `?/retry`, the library's
	// archive/unarchive actions (with their toasts + invalidateAll/goto). The
	// shared component gets the typed row, this portal's link builders, and — because
	// this user can write — the action callbacks.
	//
	// This record is now the ONLY place a firm user can restore a deleted document: the library has
	// no Archived view, so a deletion is reached again by its URL (or from the ops-side "Deleted
	// documents" page) and Restore lives here.
	//
	// Hosted by TWO routes: `/documents/[uid]` and `/clients/[uid]/documents/[doc]`. Everything that
	// differs between them arrives as a prop, so the record itself cannot drift.
	import { goto, invalidateAll } from '$app/navigation';
	import { deserialize } from '$app/forms';
	import { toast } from '@graphene/ui';
	import {
		DocumentDetail,
		type DocumentDetailActions,
		type DocumentDetailLinks
	} from '@graphene/ui/book/documents';
	import type { DocumentDetailData } from '$lib/server/document-detail';

	let {
		data,
		/** The hosting route's own path, e.g. `/clients/8/documents/doc_1`. `?/rename` and `?/retry`
		    are actions ON that route, so they post here — a hard-coded `/documents/{uid}` would
		    silently target the other route's copy. */
		actionBase,
		links,
		/** Where to go once the document is deleted — back to whichever list this was reached from,
		    since the record itself no longer belongs in the live view. */
		archivedHref
	}: {
		data: DocumentDetailData;
		actionBase: string;
		links: DocumentDetailLinks;
		archivedHref: string;
	} = $props();

	const doc = $derived(data.doc);

	// The record's injected writes: each owns its fetch + toast + reload (the shared component
	// owns the edit/pending state around them). Drafting callbacks only
	// because it takes documents:send, not documents:write (the six-splits re-key).
	const writeActions: DocumentDetailActions = {
		// Inline rename — posts the hosting route's `?/rename` action. No `document_type`: a
		// document's type is set at upload and is not changed afterwards.
		rename: async ({ name }) => {
			const body = new FormData();
			body.append('name', name);
			const res = await fetch(`${actionBase}?/rename`, { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				await invalidateAll();
				return true;
			}
			toast.error(
				result.type === 'failure'
					? String(result.data?.error ?? 'Failed to save changes.')
					: 'Failed to save changes.'
			);
			return false;
		},
		// Delete/restore stay on the library's uid-keyed actions from both hosts: they are the same
		// API call whichever list you came from, and duplicating them per route would be two more
		// permission checks to keep in step. (`?/archive` is the API's word; the UI says Delete.)
		archive: async () => {
			if (!doc) return;
			const body = new FormData();
			body.append('uid', doc.uid);
			const res = await fetch('/documents?/archive', { method: 'POST', body });
			if (!res.ok) {
				toast.error('Failed to delete the document.');
				return;
			}
			toast.success('Document deleted.');
			await goto(archivedHref);
		},
		// Deleting is recoverable (POST /documents/{uid}/unarchive restores the status the document
		// held before), so a deleted document offers Restore rather than a dead end — and this is the
		// only surface that offers it now the library's Archived view is gone.
		restore: async () => {
			if (!doc) return;
			const body = new FormData();
			body.append('uid', doc.uid);
			const res = await fetch('/documents?/unarchive', { method: 'POST', body });
			if (!res.ok) {
				toast.error('Failed to restore the document.');
				return;
			}
			await invalidateAll();
			toast.success('Document restored to the library.');
		}
	};

	// Exactly the callbacks this caller's permissions earn: drafting on documents:write. Null ⇒ the
	// record reads read-only (Download alone). `documents:send` earns nothing here now — notifying a
	// client about a document is gone, replaced by a document REQUEST.
	const actions: DocumentDetailActions | null = $derived(data.canManage ? writeActions : null);

	// Re-queue a stuck render from the detail's retry/reload affordance — posts the hosting route's
	// `?/retry` action. The API re-enqueues a failed document (or a queued one whose job was lost)
	// and no-ops one already waiting on a live job, so it is safe on either state.
	let retrying = $state(false);
	async function retry(): Promise<void> {
		if (retrying) return;
		retrying = true;
		try {
			const res = await fetch(`${actionBase}?/retry`, { method: 'POST', body: new FormData() });
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				await invalidateAll();
				toast.success('Document preparation restarted.');
			} else {
				toast.error(
					result.type === 'failure'
						? String(result.data?.error ?? 'Could not retry the document.')
						: 'Could not retry the document.'
				);
			}
		} finally {
			retrying = false;
		}
	}
</script>

<DocumentDetail
	{doc}
	events={data.events}
	previewUrl={data.previewUrl}
	previewContentType={data.previewContentType}
	loadError={data.loadError}
	{links}
	{actions}
	onretry={retry}
	onrefresh={() => invalidateAll()}
	onnavigate={(href) => goto(href)}
/>
