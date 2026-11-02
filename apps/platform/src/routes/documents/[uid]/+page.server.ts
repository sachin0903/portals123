// The library's document detail. Loader + actions live in `$lib/server/document-detail`, shared
// with `/clients/[id]/documents/[doc]` — the same record reached from the client it belongs to.
import { loadDocumentDetail, renameDocument, retryDocument } from '$lib/server/document-detail';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async (event) => ({
	record: loadDocumentDetail(event.params.uid, event)
});

export const actions: Actions = {
	rename: (event) => renameDocument(event.params.uid, event),
	retry: (event) => retryDocument(event.params.uid, event)
};
