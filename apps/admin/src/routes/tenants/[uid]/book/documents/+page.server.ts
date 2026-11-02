import { redirect } from '@sveltejs/kit';
import { authHeaders } from '$lib/api';
import { bookUrl, getJson, type BookArchivedDocument } from '$lib/book/api';
import { apiParams, itemsOf, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { PageServerLoad } from './$types';

/**
 * The filters this listing understands — the endpoint's own `_DOCUMENT_FILTERS`
 * (apps/api/app/routers/documents.py). Named so an unrelated query param (a `?focus=` deep link,
 * say) is never mistaken for one of ours and forwarded to the API.
 */
const FILTER_KEYS = ['type', 'author', 'client', 'size', 'created'] as const;

/**
 * A firm's DELETED documents — `GET /documents?status=archived` read through the book target.
 *
 * Why this page exists at all: the platform library is dropping its Live/Archived switch, so a
 * firm's own staff no longer have a surface that lists what has been deleted. Nothing is ever hard
 * deleted (`documents.documents.status = 'archived'` is a soft state, and `metadata.archived_from`
 * remembers what to restore to), so the rows are all still there — they just had no reader left.
 * This is that reader, and it lives in operations because "show me what this firm deleted" is a
 * support/oversight question, not a daily workflow.
 *
 * Vocabulary: the DB and the API say `archived`; users say DELETED. The wire parameter therefore
 * stays `status=archived` and every word on screen says Deleted. The enum is not renamed.
 *
 * SERVER-DRIVEN now, not fetched whole. The endpoint already narrows, facets and pages `?status=`
 * itself (`app/routers/documents.py::list_documents`), so filtering this page's own fetch client-side
 * would report "nothing deleted by Priya" when it only means "not on this page" — the same lie
 * `book/listing/query.ts` warns against for every paged listing. `apiParams` carries the query's own
 * `q`/`type`/`author`/`client`/`limit`/`offset`; `status=archived` is set on top of it because it
 * selects the DELETED slice rather than being one of the listing's own filters.
 *
 * Sorting stays client-side, deliberately: `list_documents` takes no `sort`/`dir` param at all, so
 * there is nothing here to hand the server, and the page's own `bind:sort` still only orders the
 * 25 rows in hand rather than the whole deleted set.
 *
 * READ-ONLY, deliberately — there is no restore here. `POST /documents/{uid}/unarchive` runs on
 * `tenant_session`, which does not accept the operations `?tenant=` param at all, so offering
 * restore would mean moving a document write onto the book-proxy path and widening the ops write
 * surface beyond the approved middle-office actions that `book.test.ts` pins to the client record.
 * That is a deliberate decision to make, not a side effect of adding a listing page.
 *
 * A failed read is a FLAG, not an empty list: "nothing deleted" rendered over a 500 is the one
 * answer this page must never give — an operator would conclude the firm's documents are intact.
 */
export const load: PageServerLoad = async ({ locals, fetch, url, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS);
	const params = apiParams(query);
	params.set('status', 'archived');
	const res = await getJson<BookArchivedDocument[] | ListingPage<BookArchivedDocument>>(
		fetch,
		bookUrl('/documents', tenant.uid, params),
		authHeaders(locals.accessToken)
	);
	// `itemsOf` rather than `.items`: `GET /documents` answers with the bare list when no `limit`
	// is asked for and the `{items, total, facets}` envelope when one is. `apiParams` always sends
	// a limit so the envelope is what arrives — but reading `.items` off a bare array yields
	// `undefined` and renders an empty table, which on THIS page reads as "this firm has deleted
	// nothing". The helper exists for precisely that ambiguity, so use it rather than assume.
	const page = res.ok ? res.data : null;
	const envelope = page && !Array.isArray(page) ? page : null;
	const documents = page ? itemsOf(page) : [];
	return {
		query,
		documents,
		// Falling back to the row count, not to zero: a bare-list answer has no `total`, and a
		// pager told there are nought matches while rows are on screen is worse than no pager.
		total: envelope?.total ?? documents.length,
		facets: envelope?.facets ?? {},
		facetLabels: envelope?.facet_labels,
		loadError: !res.ok
	};
};
