import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import {
	SHOW_OPTIONS,
	isClientListQuery,
	parseWorkFilter,
	type AmlClientPage,
	type AmlWorkItem,
	type AmlWorkQuery,
	type AmlWorkSummary
} from '$lib/aml';
import { can } from '$lib/permissions';
import {
	allowJourney,
	decideCheck,
	decideMatch,
	firmChoices,
	monitoring,
	requestPhotos,
	rerun,
	screenInPlace
} from '$lib/server/aml';
import type { Actions, PageServerLoad } from './$types';

type Read<T> = { ok: true; body: T } | { ok: false; status: number; error: string };

/** One GET from the API, with its refusal in the API's own words. */
async function read<T>(
	fetch: typeof globalThis.fetch,
	token: string | null,
	path: string
): Promise<Read<T>> {
	try {
		const res = await fetch(`${apiUrl()}${path}`, { headers: authHeaders(token) });
		if (res.ok) return { ok: true, body: (await res.json()) as T };
		return { ok: false, status: res.status, error: await detail(res) };
	} catch {
		return { ok: false, status: 503, error: 'The service is unavailable.' };
	}
}

/**
 * The work queue, worked one profile at a time: the clients the "Show" choice asks for, their
 * counts, and, on the first render only, the profile on screen (`?at=<key>`, else the queue's
 * first). The loader only reads.
 *
 * It reruns for a new "Show" or firm, and after an act, never for a move: the page moves between
 * profiles itself (shallow routing), so `at` is read untracked. A rerun is a data request, whose
 * URL can name a profile the page has since moved off, so it reads no profile and the page reads
 * the one on screen. That keeps every photo listing to a profile on screen.
 */
export const load: PageServerLoad = async ({ url, locals, fetch, isDataRequest, untrack }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:read')) error(403, 'Not authorised');
	// The client list lived here: a link with its narrowings opens it where it is now.
	if (isClientListQuery(url.searchParams)) redirect(308, `/aml/clients${url.search}`);

	const query: AmlWorkQuery = {
		...parseWorkFilter(url.searchParams),
		at: untrack(() => url.searchParams.get('at')?.trim() ?? '')
	};
	const show = SHOW_OPTIONS[query.show];
	const token = locals.accessToken;

	const asked = new URLSearchParams();
	if (show.reason) asked.set('reason', show.reason);
	if (query.tenant) asked.set('tenant', query.tenant);
	// The counts ignore the "Show" choice, so every choice carries its own count.
	const counted = new URLSearchParams();
	if (query.tenant) counted.set('tenant', query.tenant);
	const search = (params: URLSearchParams) => (params.size ? `?${params}` : '');

	const queue = read<{ items: AmlWorkItem[]; total: number; truncated: boolean }>(
		fetch,
		token,
		`/aml/work${search(asked)}`
	);
	const items = queue.then((answer) => (answer.ok ? (answer.body.items ?? []) : []));

	/** The profile on screen. A key the console no longer knows falls back to the queue's first. */
	const profile = items.then(async (list) => {
		if (isDataRequest) return { client: null, error: null };
		const first = list[0]?.key ?? null;
		const key = query.at || first;
		if (!key) return { client: null, error: null };
		const path = (k: string) => `/aml/clients/${encodeURIComponent(k)}`;
		let answer = await read<AmlClientPage>(fetch, token, path(key));
		if (!answer.ok && answer.status === 404 && first && key !== first)
			answer = await read<AmlClientPage>(fetch, token, path(first));
		return answer.ok
			? { client: answer.body, error: null }
			: { client: null, error: answer.status === 404 ? 'Client not found.' : answer.error };
	});

	const [work, summary, firms, shown] = await Promise.all([
		queue,
		read<AmlWorkSummary>(fetch, token, `/aml/work/summary${search(counted)}`),
		items.then((list) =>
			firmChoices(
				fetch,
				token,
				list.map((item) => item.firm)
			)
		),
		profile
	]);

	return {
		query,
		items: work.ok ? (work.body.items ?? []) : [],
		total: work.ok ? (work.body.total ?? 0) : 0,
		truncated: work.ok ? !!work.body.truncated : false,
		workError: work.ok ? null : work.error,
		summary: summary.ok ? summary.body : null,
		firms,
		client: shown.client,
		clientError: shown.error,
		canWrite: can(locals.permissions, locals.role, 'aml:write'),
		canAdjudicate: can(locals.permissions, locals.role, 'aml:adjudicate')
	};
};

// The acts the check sections post, shared with the client page. A screen stays on the workbench.
export const actions: Actions = {
	decideMatch,
	allowJourney,
	decideCheck,
	requestPhotos,
	monitoring,
	rerun,
	screen: screenInPlace
};
