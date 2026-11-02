import { redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

/** One row of `audit.api_log`. Bodies are absent — the list projection omits them. */
export type ApiCall = {
	id: number;
	/** How the console opens this call (`0114_api_log_uid`). The `id` beside it is the table's own
	 *  key and its sort order — a bigint on an append-only log — and never appears in a path. */
	uid: string;
	created_at: string;
	tenant_id: number | null;
	user_id: string | null;
	source: 'app' | 'sync';
	provider: string;
	/** The provider's display name (`providers.meta`), so the slug `sei` reads "SEI". */
	provider_name: string;
	service: string | null;
	transport: string;
	method: string | null;
	url: string | null;
	operation: string | null;
	status_code: number | null;
	ok: boolean;
	error: string | null;
	duration_ms: number | null;
	entity_type: string | null;
	entity_id: number | null;
};

export type ApiCallSummary = {
	provider: string;
	provider_name: string;
	service: string | null;
	source: 'app' | 'sync';
	calls: number;
	failures: number;
	p95_ms: number | null;
	last_call_at: string | null;
};

/** The product-wide listing page size (`@graphene/ui/book/listing`). */

// Filters live in the URL, not in component state: an ops user diagnosing an incident sends
// someone the link, and "the failed Seccl calls" has to survive being pasted into Slack.
export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// MULTI-VALUED: `?provider=seccl&provider=sei` ORs them (`app/platforms.py::api_calls`).
	const provider = url.searchParams.getAll('provider').filter(Boolean);
	const status = url.searchParams.get('status') ?? 'all';
	// 'app' (a request path) vs 'sync' (a provider daemon). Dropped unless it is one of those:
	// the API answers 422 on an unknown value, and losing the whole page to a stale link is a
	// worse outcome than silently showing everything.
	const rawSource = url.searchParams.get('source') ?? '';
	const source = rawSource === 'app' || rawSource === 'sync' ? rawSource : '';
	const q = url.searchParams.get('q') ?? '';
	const page = Math.max(1, Number(url.searchParams.get('page') ?? 1) || 1);

	const params = new URLSearchParams({
		limit: String(PAGE_SIZE),
		offset: String((page - 1) * PAGE_SIZE)
	});
	for (const p of provider) params.append('provider', p);
	if (status === 'failed') params.set('ok', 'false');
	if (status === 'ok') params.set('ok', 'true');
	if (source) params.set('source', source);
	if (q.trim()) params.set('q', q.trim());

	const headers = authHeaders(locals.accessToken);
	// In parallel: the summary drives the filter chips' counts and is independent of the page.
	const [callsRes, summaryRes] = await Promise.all([
		fetch(`${apiUrl()}/admin/platforms/api-calls?${params}`, { headers }),
		fetch(`${apiUrl()}/admin/platforms/api-calls/summary`, { headers })
	]);

	// A failed load is reported, not swallowed into an empty table: "no calls" and "we could not
	// read the log" look identical otherwise, and they mean opposite things.
	const loadError = callsRes.ok ? null : `${callsRes.status} ${callsRes.statusText}`;
	const body = callsRes.ok ? await callsRes.json() : { items: [], total: 0 };
	const summary: ApiCallSummary[] = summaryRes.ok ? await summaryRes.json() : [];

	return {
		calls: body.items as ApiCall[],
		total: body.total as number,
		summary,
		loadError,
		filters: { provider, status, source, q, page },
		pageSize: PAGE_SIZE
	};
};
