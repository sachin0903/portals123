import { fail, redirect } from '@sveltejs/kit';
import {
	stepRefusal,
	transferActionPath,
	type ApiTransfer,
	type StageSummary
} from '@graphene/ui/book/transfers';
import { apiParams, queryFromParams, type ListingPage } from '@graphene/ui/book/listing';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

const FILTER_KEYS = ['status', 'route', 'adviser', 'basis', 'custodian'] as const;

type Book = {
	transfers: ApiTransfer[];
	total: number;
	summary: StageSummary | null;
	facets: Record<string, string[]>;
	custodians: { code: string; name: string }[];
	loadError: boolean;
	forbidden: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const query = queryFromParams(url.searchParams, FILTER_KEYS, { sort: 'created', dir: 'desc' });
	return { query, writing, book: loadBook(fetch, locals.accessToken, tenant.uid, query) };
};

async function loadBook(
	fetch: typeof globalThis.fetch,
	accessToken: string | null,
	tenantUid: string,
	query: ReturnType<typeof queryFromParams>
): Promise<Book> {
	const result = await targetJson<
		ListingPage<ApiTransfer> & {
			summary?: StageSummary;
			custodians?: { code: string; name: string }[];
		}
	>(fetch, accessToken, tenantUid, '/transfers', apiParams(query));
	if (!result.ok) {
		return {
			transfers: [],
			total: 0,
			summary: null,
			facets: {},
			custodians: [],
			forbidden: result.status === 403,
			loadError: result.status !== 403
		};
	}
	return {
		transfers: result.data.items,
		total: result.data.total,
		summary: result.data.summary ?? null,
		facets: result.data.facets,
		custodians: result.data.custodians ?? [],
		forbidden: false,
		loadError: false
	};
}

export const actions: Actions = {
	match: async ({ locals, fetch, params }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			'/contributions/match',
			{ method: 'POST' }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		const report = (await response.json()) as { matched: number; ambiguous: number };
		return { message: `${report.matched} matched, ${report.ambiguous} need a decision.` };
	},

	step: async ({ locals, fetch, params, request }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		const step = String(form.get('step') ?? '');
		const path = transferActionPath(step, uid);
		if (!path) return fail(400, { error: 'That action is not available.' });
		const response = await targetMutation(fetch, locals.accessToken, params.uid, path, {
			method: 'POST'
		});
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		const saved = (await response.json().catch(() => null)) as {
			provider_error?: unknown;
			action_status?: unknown;
		} | null;
		if (saved?.action_status === 'awaiting_approval') return { message: 'Waiting for approval.' };
		const refusal = stepRefusal(step, saved?.provider_error);
		if (refusal) return fail(409, { error: refusal });
		return { message: 'Done.' };
	},

	note: async ({ locals, fetch, params, request }) => {
		if (!canOperateWrite(locals)) return readOnlyFailure();
		const form = await request.formData();
		const uid = String(form.get('uid') ?? '');
		const note = String(form.get('note') ?? '');
		const response = await targetMutation(
			fetch,
			locals.accessToken,
			params.uid,
			`/transfers/${encodeURIComponent(uid)}/note`,
			{ method: 'PUT', body: JSON.stringify({ note }) }
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		return { message: 'Note saved.' };
	}
};
