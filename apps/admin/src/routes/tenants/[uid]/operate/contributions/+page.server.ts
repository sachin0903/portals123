import { error, fail, redirect } from '@sveltejs/kit';
import type {
	Contribution,
	StageSummary,
	UnclassifiedCredit
} from '@graphene/ui/book/contributions';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

const FILTER_KEYS = ['status', 'adviser', 'category'] as const;

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'client', dir: 'asc' });
	return {
		query,
		focus: url.searchParams.get('focus'),
		writing,
		book: loadBook(fetch, locals.accessToken, tenant.uid, query)
	};
};

/** The platform's streamed book: the pipeline (with its whole-book stage summary) and the
 *  unclassified credits at the front of the queue. */
async function loadBook(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	query: ReturnType<typeof queryFromParams>
) {
	const [pipeline, credits] = await Promise.all([
		targetJson<ListingPage<Contribution> & { summary?: StageSummary }>(
			fetch,
			accessToken,
			tenantUid,
			'/contributions',
			apiParams(query)
		),
		targetJson<UnclassifiedCredit[]>(fetch, accessToken, tenantUid, '/contributions/unclassified')
	]);
	// Thrown inside the stream, so it lands as the page's own retry banner, not the error page.
	if (!pipeline.ok) error(pipeline.status || 500, 'Could not load contributions.');
	return {
		contributions: pipeline.data.items,
		matchCount: pipeline.data.total,
		summary: pipeline.data.summary ?? null,
		facets: pipeline.data.facets,
		credits: credits.ok ? credits.data : [],
		creditsFailed: !credits.ok
	};
}

async function post(
	event: Parameters<NonNullable<Actions[keyof Actions]>>[0],
	path: string,
	body?: unknown,
	method = 'POST'
) {
	if (!canOperateWrite(event.locals)) return readOnlyFailure();
	const response = await targetMutation(
		event.fetch,
		event.locals.accessToken,
		event.params.uid,
		path,
		{
			method,
			body: body === undefined ? undefined : JSON.stringify(body)
		}
	);
	if (!response.ok) return fail(response.status, { error: await detail(response) });
	return { ok: true };
}

export const actions: Actions = {
	classify: async (event) => {
		const form = await event.request.formData();
		const uid = String(form.get('uid') ?? '');
		const employerName = String(form.get('employer_name') ?? '').trim();
		return post(
			event,
			`/contributions/${uid}`,
			{
				category: form.get('category'),
				frequency: form.get('frequency'),
				paid_by: form.get('paid_by'),
				tax_year: form.get('tax_year'),
				...(employerName
					? {
							employer: {
								employer_name: employerName,
								address_line1: String(form.get('address_line1') ?? '') || null,
								city: String(form.get('city') ?? '') || null,
								postcode: String(form.get('postcode') ?? '') || null,
								email: String(form.get('email') ?? '') || null
							}
						}
					: {})
			},
			'PATCH'
		);
	},
	classifyCredit: async (event) => {
		const form = await event.request.formData();
		const employerName = String(form.get('employer_name') ?? '').trim();
		return post(event, '/contributions/from-transaction', {
			transaction_id: Number(form.get('transaction_id')),
			category: form.get('category'),
			frequency: form.get('frequency'),
			paid_by: form.get('paid_by'),
			tax_year: form.get('tax_year'),
			...(employerName ? { employer: { employer_name: employerName } } : {})
		});
	},
	record: async (event) =>
		post(
			event,
			`/contributions/${String((await event.request.formData()).get('uid') ?? '')}/record-to-morgan-lloyd`
		),
	lodge: async (event) =>
		post(
			event,
			`/contributions/${String((await event.request.formData()).get('uid') ?? '')}/lodge-expectation`
		),
	refresh: async (event) =>
		post(
			event,
			`/contributions/${String((await event.request.formData()).get('uid') ?? '')}/refresh-morgan-lloyd`
		),
	cancel: async (event) => {
		const form = await event.request.formData();
		return post(event, `/contributions/${String(form.get('uid') ?? '')}/cancel`, {
			reason: String(form.get('reason') ?? '')
		});
	},
	match: async (event) => post(event, '/contributions/match')
};
