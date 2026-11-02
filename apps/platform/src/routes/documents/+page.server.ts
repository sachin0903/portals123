import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import type { ActionEvent } from '@graphene/ui';
import {
	PAGE_SIZE,
	apiParams,
	itemsOf,
	queryFromParams,
	type ListingPage,
	type ListingQuery
} from '@graphene/ui/book/listing';
import {
	fileToRow,
	toRow,
	type ApiDocument,
	type ApiFile,
	type ClientOption,
	type DocumentTypeOption,
	type LibraryRow
} from '@graphene/ui/book/documents';
import type { Actions, PageServerLoad } from './$types';

/** Cap the library fetch: with `limit` the API returns a page envelope. 200 is the API ceiling.
 *
 *  The cap now bounds the MATCH SET, not the book: search and the three filters are applied by the
 *  API before this cut, so narrowing reaches every document rather than only the first 200 that
 *  happened to be fetched. Paging within the answer stays client-side, because the library is one
 *  list built from TWO endpoints and a merged `total` cannot drive one pager. */
const LIBRARY_CAP = 200;

/** The filters this listing understands. Named so an unrelated query param is never mistaken for
 *  one and forwarded to the API. Mirrors `_DOCUMENT_FILTERS` / `_FILE_FILTERS` on the API side. */
const FILTER_KEYS = ['type', 'author', 'client', 'size', 'created'] as const;
const MAX_UPLOAD_FILES = 10;

/** Everything the library has to fetch, in one streamed object. The permissions are NOT in here:
 *  they decide what the page renders at all, and they are known without a single fetch. */
type Library = {
	documents: LibraryRow[];
	clients: ClientOption[];
	documentTypes: DocumentTypeOption[];
	activity: ActionEvent[];
	/** The API's facet lists, UNIONED across both endpoints — the library is one list from two, so a
	 *  Type option present only among supporting files still has to be offered. */
	facets: Record<string, string[]>;
	/** Readable names for facet values that are ids — today just `client`, whose options are uids.
	 *  They come from the API rather than from the rows because the client facet is computed with
	 *  the client filter LIFTED: labelling from the loaded rows would resolve only the client already
	 *  filtered to and show every other option as a bare nanoid. */
	facetLabels: Record<string, Record<string, string>>;
	/** How many rows match across the whole book, summed over both endpoints. */
	total: number;
	loadError: boolean;
};

const EMPTY_LIBRARY: Library = {
	documents: [],
	clients: [],
	documentTypes: [],
	activity: [],
	facets: {},
	facetLabels: {},
	total: 0,
	loadError: false
};

/** Merge the two endpoints' facet-label maps. A uid appears in at most one of them in practice
 *  (a client's documents and their supporting files carry the same name), so last-one-wins is fine. */
function mergeLabels(
	...maps: (Record<string, Record<string, string>> | undefined)[]
): Record<string, Record<string, string>> {
	const out: Record<string, Record<string, string>> = {};
	for (const map of maps) {
		for (const [key, labels] of Object.entries(map ?? {})) {
			out[key] = { ...(out[key] ?? {}), ...labels };
		}
	}
	return out;
}

/** Union two facet maps key by key, de-duplicated and collated the way the API sorts them. */
function mergeFacets(...maps: Record<string, string[]>[]): Record<string, string[]> {
	const out: Record<string, Set<string>> = {};
	for (const map of maps) {
		for (const [key, values] of Object.entries(map ?? {})) {
			const set = (out[key] ??= new Set<string>());
			for (const value of values) set.add(value);
		}
	}
	return Object.fromEntries(
		Object.entries(out).map(([key, values]) => [
			key,
			[...values].sort((a, b) => a.localeCompare(b, 'en-GB', { sensitivity: 'base' }))
		])
	);
}

export const load: PageServerLoad = async ({ locals, fetch, depends, url }) => {
	if (!locals.user) redirect(303, '/login');
	// The firm-wide library takes documents:show on top of documents:read (the sidebar hides the row
	// without it); someone arriving by URL is told why. The LOAD only: the actions below stay on
	// their own write/send gates, because the client tab and the document record post to them too.
	if (!can(locals.permissions, locals.role, 'documents:show')) {
		error(403, 'You do not have permission to view the Documents page.');
	}
	depends('app:documents');
	// Resolved from the URL, synchronously, so the toolbar renders with its filters already applied
	// while the book itself is still streaming.
	// `offset: 0` ALWAYS. `queryFromParams` honours `?offset=` — right for a listing whose pager
	// writes it, wrong here: this library pages client-side over the whole answer and never sets it,
	// so a stale or hand-edited offset would drop that many rows off the front of BOTH endpoints
	// while the pager still showed page 1, with nothing on screen saying so.
	const query = { ...queryFromParams(url.searchParams, FILTER_KEYS, LIBRARY_CAP), offset: 0 };
	// Drafting (upload/rename/archive) is documents:write; RELEASING to a client
	// (send, requests) is documents:send — the six-splits re-key. Neither implies the other.
	const canManage = can(locals.permissions, locals.role, 'documents:write');
	const canSend = can(locals.permissions, locals.role, 'documents:send');
	// The "Recent activity" glance reads the action-event stream, so it is EVENT data, gated by
	// activity:read (not documents:read). Skip the fetch AND hide the panel when the caller lacks it,
	// rather than firing a 403 that renders as a permanently-empty card.
	const canReadActivity = can(locals.permissions, locals.role, 'activity:read');
	const canRead = can(locals.permissions, locals.role, 'documents:read');
	// The library is STREAMED — five parallel reads, so this is the page a sidebar click waited
	// longest on. Everything above is resolved from the session, so the page's own chrome (and every
	// permission gate on it) renders immediately.
	return {
		canManage,
		canSend,
		canReadActivity,
		query,
		library: canRead
			? loadLibrary(locals.accessToken, fetch, query, { canManage, canSend, canReadActivity })
			: Promise.resolve(EMPTY_LIBRARY)
	};
};

async function loadLibrary(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: ListingQuery,
	{
		canManage,
		canSend,
		canReadActivity
	}: { canManage: boolean; canSend: boolean; canReadActivity: boolean }
): Promise<Library> {
	const headers = authHeaders(accessToken);
	// The SAME query goes to both halves of the list. A filter that reached only /documents would
	// silently drop every supporting file instead of narrowing it.
	const params = apiParams(query).toString();
	// One slice: the live book. The archived (now: DELETED) slice is no longer fetched — the library
	// has no view onto it, and a firm reaches a deleted document by its own URL; browsing them is the
	// ops-side "Deleted documents" page.
	const [docsR, filesR, clientsR, typesR, activityR] = await Promise.all([
		fetchJson<ListingPage<ApiDocument> | ApiDocument[]>(
			fetch,
			`${apiUrl()}/documents?${params}`,
			headers
		),
		fetchJson<ListingPage<ApiFile> | ApiFile[]>(fetch, `${apiUrl()}/files?${params}`, headers),
		// The client book feeds the upload dialog's made-for picker (write) and the request
		// dialog's client list (send) — fetched for whichever the caller holds.
		canManage || canSend
			? fetchJson<ClientOption[]>(fetch, `${apiUrl()}/documents/clients`, headers)
			: null,
		canManage
			? fetchJson<DocumentTypeOption[]>(fetch, `${apiUrl()}/documents/types`, headers)
			: null,
		// Recent activity draws from the action-event stream (`document_family`), humanised by the
		// shared EventFeed — one vocabulary with the /events page. Fetched ONLY when the caller holds
		// activity:read; otherwise skipped (`null`) and the panel is hidden.
		canReadActivity
			? fetchJson<{ items: ActionEvent[] }>(
					fetch,
					`${apiUrl()}/firm/audit/actions?document_family=true&limit=${PAGE_SIZE}`,
					headers
				)
			: null
	]);
	// A failed fetch surfaces as a retry banner rather than an empty page — a transient 401 at the
	// token-refresh boundary must not read as "no documents". (`null` = permission-gated skip.)
	// `activityR` is deliberately EXCLUDED from loadError: the glance degrades to an empty/hidden
	// panel, never a permanent (un-retryable) banner over a fully loaded page.
	const loadError = [docsR, filesR, clientsR, typesR].some((r) => r !== null && !r.ok);
	const files = filesR.ok ? itemsOf(filesR.data) : [];
	const clients = clientsR?.ok ? clientsR.data : [];
	const documentTypes = typesR?.ok
		? typesR.data.map((t) => ({ kind: t.kind, slug: t.slug, label: t.label }))
		: [];
	const activity = activityR?.ok ? (activityR.data.items ?? []) : [];
	const liveRows = (docsR.ok ? itemsOf(docsR.data) : []).map(toRow);
	const envelope = (r: typeof docsR | typeof filesR) =>
		r.ok && !Array.isArray(r.data) ? r.data : null;
	const docsPage = envelope(docsR);
	const filesPage = envelope(filesR);
	return {
		// One list: financial documents + supporting files (passports, ID…), distinguished by isFile.
		documents: [...liveRows, ...files.map(fileToRow)],
		clients,
		documentTypes,
		activity,
		facets: mergeFacets(docsPage?.facets ?? {}, filesPage?.facets ?? {}),
		facetLabels: mergeLabels(docsPage?.facet_labels, filesPage?.facet_labels),
		total: (docsPage?.total ?? liveRows.length) + (filesPage?.total ?? files.length),
		loadError
	};
}

export const actions: Actions = {
	/** Upload one or more documents: forward every multipart `file` part to POST /documents/upload.
	 *
	 * The dropzone accepts a set, so the action does too — posting only the first file used to lose
	 * the rest silently behind a success toast. The endpoint takes a repeated `file` part and answers
	 * with an `UploadResult` (`documents` stored + `rejected` named), so a partly-failed drop can be
	 * reported per file rather than as a blanket success. Nothing landing at all is the API's own
	 * 422, which surfaces here as a failure. */
	upload: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to upload documents.' });
		}
		const form = await request.formData();
		// A ZERO-BYTE part is forwarded, not dropped. Filtering `size > 0` here meant an empty file in a
		// multi-file drop never reached the API, so it never appeared in the API's `rejected` list and
		// the user was toasted a clean success for a file that vanished. The API's validator refuses
		// empty content (`document must be 1..N bytes`) and names the file, which is the honest report.
		// A zero-byte part with no filename is a browser's unfilled file input rather than a file, so
		// that one is still dropped.
		const files = form
			.getAll('file')
			.filter((f): f is File => f instanceof File)
			.filter((f) => f.size > 0 || f.name !== '');
		if (files.length === 0) return fail(400, { error: 'Choose a file to upload.' });
		// Mirrors the API's MAX_UPLOAD_FILES: refuse here rather than streaming a body the request-size
		// ceiling will reject anyway.
		if (files.length > MAX_UPLOAD_FILES) {
			return fail(422, {
				error: `Upload no more than ${MAX_UPLOAD_FILES} files at a time. Add the rest in another batch.`
			});
		}
		// Type is required and open-ended (a registry tag or a brand-new one the API registers); no
		// default — the dialog forces a specific choice, and the API rejects blank/'auto'/'other'.
		const documentType = String(form.get('document_type') ?? '').trim();
		if (!documentType) return fail(400, { error: 'Choose a document type.' });
		// A single explicit name only makes sense for a single file; a multi-file drop keeps each
		// file's own name (the API applies `name` only when one part was sent).
		const name = files.length === 1 ? String(form.get('name') ?? '').trim() : '';

		// The client this upload is made for (0..1; filing only — visibility still needs a send).
		const subjectContactId = String(form.get('subject_contact_id') ?? '').trim();

		const body = new FormData();
		for (const file of files) body.append('file', file, file.name);
		body.append('document_type', documentType);
		if (name) body.append('name', name);
		if (subjectContactId) body.append('subject_contact_id', subjectContactId);
		// Multipart: send only the auth header and let fetch set the boundary Content-Type.
		const res = await fetch(`${apiUrl()}/documents/upload`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken),
			body
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const result = (await res.json().catch(() => null)) as {
			documents?: unknown[];
			rejected?: { filename?: string | null; reason?: string }[];
		} | null;
		const failures = (result?.rejected ?? []).map(
			(r) => `${r.filename ?? 'file'}: ${r.reason ?? 'could not be stored'}`
		);
		// Report what the API says it stored, never the number of parts we posted.
		const uploaded = Array.isArray(result?.documents)
			? result.documents.length
			: files.length - failures.length;
		return { ok: true, uploaded, failures };
	},

	/** Suggest a document type from a filename (POST /classify-type) — a hint for the upload dialog's
	 * "Auto-detect", never a save. Returns a blank suggestion when nothing matches. */
	classify: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'Not allowed.' });
		}
		const filename = String((await request.formData()).get('filename') ?? '').trim();
		const res = await fetch(`${apiUrl()}/documents/classify-type`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ filename })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const data = (await res.json().catch(() => null)) as {
			document_type?: string;
			label?: string;
		} | null;
		return { document_type: data?.document_type ?? '', label: data?.label ?? '' };
	},

	// `notify` stood here — it posted the client-notice (`POST /documents/{uid}/notify`). Gone with
	// the feature, endpoint and all: a document REQUEST asks a client to act on a document and
	// records whether they did, which is the same job done once instead of twice.

	/** Deliver a document to one or more clients (links them as recipients).
	 *
	 * `documents:send`, not `documents:write` (the six-splits re-key): putting a document in
	 * front of a client is release, not drafting — the API's send endpoint demands the same.
	 *
	 * NOTHING IN THE UI POSTS HERE ANY MORE. Kept because a send is still a real act with a real
	 * meaning — `role='recipient'` is the only record that a document was actually delivered, which
	 * `sent_to_client`/`sent_at`/`recipients` and the worker's client context all read — and because
	 * the endpoint behind it is untouched. Nothing above it offers a client-facing
	 * document act any more either — a REQUEST is the surface for that. */
	send: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:send')) {
			return fail(403, { error: 'You do not have permission to send documents.' });
		}
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '').trim();
		const contactIds = form
			.getAll('contact_ids')
			.map((v) => Number(v))
			.filter((n) => Number.isFinite(n));
		if (!uid) return fail(400, { error: 'No document specified.' });
		if (contactIds.length === 0) return fail(400, { error: 'Choose at least one recipient.' });
		const message = String(form.get('message') ?? '').trim();
		const res = await fetch(`${apiUrl()}/documents/${uid}/send`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ contact_ids: contactIds, message: message || null })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		// Report the API's own counts, never the number we asked for: a recipient the API dropped
		// (not visible in the caller's book) must not read back as "sent", and a recipient with no
		// email address gains portal visibility but is told nothing — `notNotified` names them so the
		// dialog can stop promising everyone an email.
		const result = (await res.json().catch(() => null)) as {
			sent?: number;
			notified?: number;
			not_notified?: string[];
		} | null;
		const sent = typeof result?.sent === 'number' ? result.sent : contactIds.length;
		const notified = typeof result?.notified === 'number' ? result.notified : sent;
		const notNotified = Array.isArray(result?.not_notified)
			? result.not_notified.map((n) => String(n))
			: [];
		return { ok: true, sent, requested: contactIds.length, notified, notNotified };
	},

	/** Soft-archive a document (drops it off the live list; reversible via `unarchive`).
	 *
	 * The UI calls this DELETE — the endpoint, the enum and this action keep the word `archive`,
	 * because renaming a DB state to follow a label is how a schema starts lying. What the user is
	 * told and what the row says are allowed to differ; what they must not do is disagree about the
	 * effect, which is why the confirm copy promises no permanence. */
	archive: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to archive documents.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No document specified.' });
		const res = await fetch(`${apiUrl()}/documents/${uid}/archive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Restore an archived document to the live library (POST /documents/{uid}/unarchive).
	 *
	 * Archiving is recoverable — the API remembers the status the document held (`archived_from`) and
	 * puts it back. The only surface that reaches this now is the document RECORD's Restore button
	 * (both of its hosts post here): the library's Archived view is gone, so there is no listing of
	 * deleted documents to restore from and no bulk restore. */
	unarchive: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to restore documents.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No document specified.' });
		const res = await fetch(`${apiUrl()}/documents/${uid}/unarchive`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	},

	/** Requeue a failed render (POST /documents/{uid}/retry). */
	retryRender: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to regenerate documents.' });
		}
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, { error: 'No document specified.' });
		const res = await fetch(`${apiUrl()}/documents/${uid}/retry`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
