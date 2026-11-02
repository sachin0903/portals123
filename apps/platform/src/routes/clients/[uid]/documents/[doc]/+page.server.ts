// One of this client's documents, kept under the client in the URL rather than bouncing out to the
// flat `/documents/{uid}`. Same loader and same actions as the library's detail — the only
// difference is `contactId`, which makes the route refuse a document that is not this client's.
//
// The client is named in the URL by their uid; `contactId` is the SERIAL the documents API filters
// on. It comes from the record the client layout already loaded, so the scoping check costs no
// extra round trip.
import { loadDocumentDetail, renameDocument, retryDocument } from '$lib/server/document-detail';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async (event) => {
	const { detail } = await event.parent();
	return { record: loadDocumentDetail(event.params.doc, event, detail.contact.id) };
};

export const actions: Actions = {
	rename: (event) => renameDocument(event.params.doc, event),
	retry: (event) => retryDocument(event.params.doc, event)
};
