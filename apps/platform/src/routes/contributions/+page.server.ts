import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson, jsonHeaders } from '$lib/api';
import {
	UNPUSHABLE_CATEGORIES,
	type Contribution,
	type ContributionCategory,
	type UnclassifiedCredit
} from '@graphene/ui/book/contributions';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import type { StageSummary } from '@graphene/ui/book/contributions';
import type { Actions, PageServerLoad } from './$types';

/** The filters this workbench understands. Named so `?focus=` is never forwarded to the API. */
const FILTER_KEYS = ['status', 'adviser', 'category'] as const;

/** The API's error body: FastAPI's `{ detail: "..." }`. */
async function detailOf(res: Response, fallback: string): Promise<string> {
	try {
		const body = (await res.json()) as { detail?: unknown };
		return typeof body.detail === 'string' ? body.detail : fallback;
	} catch {
		return fallback;
	}
}

/** Everything the workbench has to fetch, in one streamed object. */
type Book = {
	contributions: Contribution[];
	matchCount: number;
	summary: StageSummary | null;
	facets: Record<string, string[]>;
	credits: UnclassifiedCredit[];
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'contributions:read')) {
		error(403, 'You do not have access to contributions.');
	}

	// Both halves of the flow in one round trip: the pipeline (contributions already booked or
	// classified) and the credits that arrived with nothing said about them. They are separate
	// endpoints because they are separate tables — `platform.contributions` and the unclaimed
	// slice of `platform.transactions` — but a workbench that loaded them in sequence would show
	// an empty "to classify" tab for a beat on every navigation.
	// The pipeline is paged and filtered server-side from the URL. The unclassified credits are
	// NOT paged: they are the front of the queue and the reason an adviser opened the page, so
	// hiding some behind a pager would bury the work.
	//
	// The query and the permission are resolved here and the rows are STREAMED: the page renders
	// on the click with its toolbar already showing the filters in the URL. `redirect`/`error`
	// stay out here — they must be thrown before the response starts, not from a streamed promise.
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'client', dir: 'asc' });
	return {
		query,
		focus: url.searchParams.get('focus'),
		book: loadBook(locals.accessToken, fetch, query)
	};
};

async function loadBook(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: ReturnType<typeof queryFromParams>
): Promise<Book> {
	const [pipeline, credits] = await Promise.all([
		fetchJson<ListingPage<Contribution> & { summary?: StageSummary }>(
			fetch,
			`${apiUrl()}/contributions?${apiParams(query)}`,
			authHeaders(accessToken)
		),
		fetchJson<UnclassifiedCredit[]>(
			fetch,
			`${apiUrl()}/contributions/unclassified`,
			authHeaders(accessToken)
		)
	]);

	if (!pipeline.ok) {
		// A failed read is thrown from inside the stream, so it surfaces as the page's own retry
		// banner rather than SvelteKit's error page: the toolbar stays on screen.
		error(pipeline.status || 500, 'Could not load contributions');
	}

	return {
		contributions: pipeline.data.items,
		matchCount: pipeline.data.total,
		// WHOLE-BOOK stage totals, computed before the filters and before the page cut. Absent from
		// an older API, in which case the strip falls back to the page — worse, but not broken.
		summary: pipeline.data.summary ?? null,
		facets: pipeline.data.facets,
		// Degrade rather than fail the page: an older API without the unclassified endpoint still
		// serves the pipeline, and an empty queue is honest there (nothing is being hidden — the
		// endpoint does not exist to hide anything).
		credits: credits.ok ? credits.data : []
	};
}

/**
 * CLASSIFYING RECORDS IT. One act, two calls.
 *
 * Saving the classification and pushing to Morgan Lloyd were two presses in the panel with a
 * `confirmed` state in between — a state that badges as done while nobody downstream has been told,
 * and which an adviser working through twenty payments will leave behind on some of them. On an
 * SEI SIPP they are one decision: classifying the credit records it with Morgan Lloyd. A Seccl
 * account is administered by Seccl, so that call is skipped.
 *
 * Chained HERE rather than in the API, deliberately. `PATCH /contributions/{uid}` writes our row
 * and `POST …/record-to-morgan-lloyd` calls the vendor for an SEI SIPP, so ops keeps a door to
 * each, the vendor's own status codes reach us unflattened, and a failed push cannot roll back a
 * classification that is correct and ours.
 *
 * A failure is a WARNING on a success, not a failure: the contribution is written either way, and
 * the row stays in "Not lodged", which is the metric that exists to count exactly this.
 */
async function recordToMorganLloyd(
	fetch: typeof globalThis.fetch,
	token: string | null | undefined,
	saved: Record<string, unknown>,
	classified: string
): Promise<{ message: string } | { warning: string }> {
	const category = String(saved.category ?? '') as ContributionCategory;
	// Morgan Lloyd has no concept of a transfer-in or of a credit that was not a contribution, and
	// `not_a_contribution` is cancelled in the same call that writes it. Neither is a failure to
	// report — mirrors `contributions.py::UNPUSHABLE`.
	if (UNPUSHABLE_CATEGORIES.has(category)) return { message: classified };
	// Morgan Lloyd administers SEI SIPPs. A Seccl account, including a Seccl SIPP, is
	// administered by Seccl, so classifying it is not a call to Morgan Lloyd.
	if (saved.custodian !== 'sei' || saved.is_pension !== true) return { message: classified };
	if (saved.ml_contribution_id) return { message: `${classified} Already at Morgan Lloyd.` };

	const res = await fetch(`${apiUrl()}/contributions/${String(saved.uid)}/record-to-morgan-lloyd`, {
		method: 'POST',
		headers: jsonHeaders(token ?? null)
	});
	if (res.ok) return { message: `${classified} Recorded to Morgan Lloyd.` };
	// The vendor's own words, verbatim: a 422 names the field that is missing and a 503 says the
	// integration is unconfigured, and both are things the adviser or an operator can act on.
	return {
		warning: `${classified} Not recorded to Morgan Lloyd — ${await detailOf(
			res,
			'the call did not succeed.'
		)}`
	};
}

/** POST to the API and turn a failure into the form's `{ message }`. */
async function call(
	fetch: typeof globalThis.fetch,
	token: string | null | undefined,
	path: string,
	fallback: string,
	init: RequestInit = {}
) {
	const res = await fetch(`${apiUrl()}${path}`, {
		method: 'POST',
		headers: jsonHeaders(token ?? null),
		...init
	});
	if (!res.ok) return fail(res.status, { message: await detailOf(res, fallback) });
	return null;
}

export const actions: Actions = {
	/** Save the HMRC classification and record it to Morgan Lloyd. Gross and relief are recomputed
	 *  server-side; the employer, where one is needed, is read from the client's own record. */
	classify: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');

		// The category and the payer it implies, and nothing else. No `frequency` and no `tax_year`:
		// `categorise` COALESCEs an omitted parameter, so leaving them out keeps what was worked out
		// at creation rather than overwriting it. No contributor block either — an employer is a
		// fact about the CLIENT, held on `crm.contacts.employer_name` and read there by the API.
		const res = await fetch(`${apiUrl()}/contributions/${uid}`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ category: form.get('category'), paid_by: form.get('paid_by') })
		});
		if (!res.ok) {
			return fail(res.status, {
				message: await detailOf(res, 'Could not save the classification.')
			});
		}
		return recordToMorganLloyd(
			fetch,
			locals.accessToken,
			await res.json(),
			'Classification saved.'
		);
	},

	/** Classify a credit that already landed — path A. Creates the contribution against the
	 *  money, with no SEI expectation: a forecast for a payment already received is a row the
	 *  matcher can never resolve. */
	classifyCredit: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const form = await request.formData();
		const res = await fetch(`${apiUrl()}/contributions/from-transaction`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				transaction_id: Number(form.get('transaction_id')),
				category: form.get('category'),
				paid_by: form.get('paid_by')
			})
		});
		if (!res.ok) {
			return fail(res.status, { message: await detailOf(res, 'Could not classify that credit.') });
		}
		// The same chain as `classify`, because it is the same act: the credit is now a
		// contribution, and the administrator should have it.
		return recordToMorganLloyd(fetch, locals.accessToken, await res.json(), 'Credit classified.');
	},

	/** Retry the SEI payment expectation. */
	lodge: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const uid = String((await request.formData()).get('uid') ?? '');
		const failure = await call(
			fetch,
			locals.accessToken,
			`/contributions/${uid}/lodge-expectation`,
			'Could not lodge the expectation with the custodian.'
		);
		return failure ?? { message: 'Expectation lodged with the custodian.' };
	},

	/** Stop the contribution, recording why, and cancel its open expectation. */
	cancel: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		const failure = await call(
			fetch,
			locals.accessToken,
			`/contributions/${uid}/cancel`,
			'Could not cancel the contribution.',
			{ body: JSON.stringify({ reason: String(form.get('reason') ?? '') }) }
		);
		return failure ?? { message: 'Contribution cancelled.' };
	},

	/** Re-read `cam_status` from Morgan Lloyd. This is what moves a contribution through
	 *  Application → Active → Received: those transitions happen at the scheme administrator and
	 *  at HMRC, so asking is the only way to learn them. */
	refresh: async ({ request, locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const uid = String((await request.formData()).get('uid') ?? '');
		const failure = await call(
			fetch,
			locals.accessToken,
			`/contributions/${uid}/refresh-morgan-lloyd`,
			'Could not read the status from Morgan Lloyd.'
		);
		return failure ?? { message: 'Status refreshed from Morgan Lloyd.' };
	},

	/** Match open expectations to the credits that fulfilled them — path B. */
	match: async ({ locals, fetch }) => {
		if (!locals.user) redirect(303, '/login');
		const res = await fetch(`${apiUrl()}/contributions/match`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken)
		});
		if (!res.ok) {
			return fail(res.status, { message: await detailOf(res, 'Could not run the matcher.') });
		}
		const report = (await res.json()) as { matched: number; ambiguous: number };
		// Report the ambiguous count too: it is work for a human, and a matcher that silently
		// left money unreconciled would look like it had finished.
		const parts = [`${report.matched} matched`];
		if (report.ambiguous > 0) parts.push(`${report.ambiguous} need a decision`);
		return { message: parts.join(', ') + '.' };
	}

	// NB: there is deliberately no `status` action. `POST /contributions/{uid}/status` exists for
	// the feed and for ops, but "hold" and "flag for manual" are not moves an adviser makes — the
	// pipeline advances because a custodian or the back office did something, not because someone
	// pressed a button. `manual_intervention` still renders if it arrives from upstream.
};
