import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson, type Client } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import { apiParams, itemsOf, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { Actions, PageServerLoad } from './$types';
import { FILTER_KEYS, SORT } from './listing';

/** GET /crm/contacts as it responds now: the per-client value counts only accounts that can hold
 *  value, and states when that value was written (see crm.py's provenance note). */
export type ClientRow = Client & {
	valued_account_count: number;
	value_as_of: string | null;
};

/** Whether an address-book email is well-formed enough to send to. Deliberately permissive —
 *  it rejects the typos a person makes, not the exotic-but-legal RFC cases. */
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** The book the listing renders — everything that has to be fetched, in one streamed object. */
type Book = {
	clients: ClientRow[];
	matchCount: number;
	facets: Record<string, string[]>;
	facetCounts: Record<string, Record<string, number>>;
	/** How far each range filter's column runs — the slider's track. See `ListingPage.bounds`. */
	bounds: Record<string, number[]>;
	archived: ClientRow[];
	valuesAsOf: string | null;
	loadError: boolean;
	archivedError: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// The query and the permission are resolved SYNCHRONOUSLY and the rows are STREAMED: the page
	// renders the moment it is navigated to (its toolbar already showing the filters in the URL) and
	// fills in when the API answers, rather than the click buying a second of the previous page.
	// `redirect` stays out here — it has to be thrown before the response starts, not from inside a
	// streamed promise.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, SORT);
	const book = loadBook(locals.accessToken, fetch, url, query);
	return {
		query,
		canManage: can(locals.permissions, locals.role, 'contacts:write'),
		canCreate: can(locals.permissions, locals.role, 'contacts:create'),
		book
	};
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	url: URL,
	query: ReturnType<typeof queryFromParams>
): Promise<Book> {
	// RLS scopes the rows to what this caller may see (whole firm for a read-all adviser,
	// their own book otherwise). A failure comes back as a flag: rendering "No clients yet" to an
	// adviser with a full book because the API 500'd is the worst thing this page can do.
	// The closed set is fetched alongside the live book so the Closed filter can reopen from here.
	// A failure to load it is reported separately: an empty Closed list must not be able to mean
	// "the read failed".
	//
	// The live book is paged and filtered SERVER-SIDE from the URL: a filter applied to one page
	// would report "no client called Halloway" when it means "not on this page". The closed set is
	// fetched whole and stays locally filtered — it is a different population, not a narrowing of
	// this one, and it is small by nature.
	const archivedView =
		url.searchParams.get('segment') === 'closed' || url.searchParams.get('segment') === 'archived';
	const [res, archivedRes] = await Promise.all([
		fetchJson<ListingPage<ClientRow>>(
			fetch,
			`${apiUrl()}/crm/contacts?${apiParams(query)}`,
			authHeaders(accessToken)
		),
		archivedView
			? fetchJson<ListingPage<ClientRow>>(
					fetch,
					`${apiUrl()}/crm/contacts?archived=true&limit=200`,
					authHeaders(accessToken)
				)
			: Promise.resolve({
					ok: true as const,
					data: { items: [] as ClientRow[], total: 0, facets: {} }
				})
	]);
	const page: ListingPage<ClientRow> = res.ok ? res.data : { items: [], total: 0, facets: {} };
	const clients = page.items;
	// The newest as-at across the book — the whole list is one batch snapshot, so one stamp is
	// honest for the page header.
	const valuesAsOf = clients.reduce<string | null>(
		(latest, c) => (c.value_as_of && (!latest || c.value_as_of > latest) ? c.value_as_of : latest),
		null
	);
	return {
		clients,
		matchCount: page.total,
		facets: page.facets,
		facetCounts: page.facet_counts ?? {},
		bounds: page.bounds ?? {},
		archived: archivedRes.ok ? itemsOf(archivedRes.data) : [],
		valuesAsOf,
		loadError: !res.ok,
		archivedError: !archivedRes.ok
	};
}

/** Every branch of ?/createClient returns this shape, so the page can read `field`/`existingUid`
 *  without TypeScript narrowing a union that only some branches carry. */
type CreateResult = {
	error: string | null;
	/** The field to mark invalid, when the failure is a field-level one. */
	field: 'email' | null;
	/** The client already holding that email, so the page can offer a link to them. */
	existingUid: string | null;
	uid: string | null;
};
const problem = (error: string, extra: Partial<CreateResult> = {}): CreateResult => ({
	error,
	field: null,
	existingUid: null,
	uid: null,
	...extra
});

export const actions: Actions = {
	/** Reopen a closed client. Also offered on the record now (Closed stays loadable); the listing
	 *  keeps it because this is where you find everyone who has been closed. */
	restoreClient: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'contacts:write')) redirect(303, '/clients');
		const uid = String((await request.formData()).get('uid') ?? '');
		if (!uid) return fail(400, problem('No client specified.'));
		const res = await fetch(`${apiUrl()}/firm/contacts/${uid}/restore`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, problem(await detail(res)));
		return { error: null, field: null, existingUid: null, uid: null };
	},

	createClient: async ({ request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'contacts:create')) redirect(303, '/clients');
		const data = await request.formData();
		const type = String(data.get('type') ?? 'individual');
		const isOrg = type === 'organisation';
		const name = String(data.get('name') ?? '').trim();
		const firstName = String(data.get('first_name') ?? '').trim();
		const surname = String(data.get('surname') ?? '').trim();
		const email = String(data.get('email') ?? '').trim();
		const subType = isOrg ? String(data.get('sub_type') ?? '').trim() : '';
		if (isOrg ? !name : !surname)
			return fail(
				400,
				problem(isOrg ? 'An organisation name is required.' : 'A surname is required.')
			);
		if (subType && !['company', 'trust', 'charity'].includes(subType))
			return fail(400, problem('Pick a company, trust or charity.'));
		if (email && !EMAIL_RE.test(email))
			return fail(400, problem('That doesn’t look like an email address.', { field: 'email' }));

		// Duplicate check against the book this caller can see. RLS means a scope-limited adviser
		// can't be warned about a clash outside their book, so this catches the common case (the
		// same client added twice) and not every possible one — the API has no uniqueness rule.
		// Search by the email rather than paging the whole book: a first-page miss would silently
		// create a second client, and `/crm/contacts` without `limit` is a full scan we don't need.
		if (email) {
			const existing = await fetchJson<ClientRow[] | ListingPage<ClientRow>>(
				fetch,
				`${apiUrl()}/crm/contacts?q=${encodeURIComponent(email)}`,
				authHeaders(locals.accessToken)
			);
			if (existing.ok) {
				const clash = itemsOf(existing.data).find(
					(c) => c.email?.toLowerCase() === email.toLowerCase()
				);
				if (clash)
					return fail(
						409,
						problem(`${clash.email} is already on this book. Open the existing client instead.`, {
							field: 'email',
							existingUid: clash.uid
						})
					);
			}
		}

		const body = {
			type,
			sub_type: subType || null,
			first_name: isOrg ? null : firstName || null,
			surname: isOrg ? null : surname,
			// `name` is the generic display name every downstream consumer falls back to (the
			// dashboard attention rail, documents.requests.contact_name). It used to be forced to
			// null for individuals, which is why platform-created people rendered as "Client".
			name: isOrg ? name : [firstName, surname].filter(Boolean).join(' '),
			email: email || null,
			// Creation asks for the mobile. The other number is on the record.
			mobile: String(data.get('mobile') ?? '').trim() || null
		};
		const res = await fetch(`${apiUrl()}/firm/contacts`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify(body)
		});
		if (!res.ok) return fail(res.status, problem(await detail(res)));
		// Hand the new uid back so the page can take the adviser straight to the record. The serial
		// `id` is never a portal URL.
		const created: { uid?: string } = await res.json();
		return { error: null, field: null, existingUid: null, uid: created.uid ?? null };
	}
};
