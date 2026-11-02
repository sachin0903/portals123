/**
 * Journey insights — the step funnel for one published version.
 *
 * Its own page rather than a tab inside the builder: the builder is a canvas that owns the whole
 * viewport and this is a report you leave open beside it, the cohort (version + window) belongs in
 * the URL so a colleague can be sent one, and the two have different permissions in prospect
 * (reading how a journey performs is not authoring it).
 *
 * A thin mount, like every other book page: the loader fetches, `@graphene/ui/book/journeys`'s
 * `FlowFunnel` renders, and the admin portal will mount the same component over its own
 * `?tenant=`-scoped loader.
 */
import { redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { FlowInsights } from '@graphene/ui/book/journeys';
import type { PageServerLoad } from './$types';

/** Kept in step with `RANGE_PRESETS` in @graphene/ui; anything else falls back to 90 days rather
 *  than being passed through, so a hand-edited URL cannot ask the API for a five-year aggregate. */
const ALLOWED_WINDOWS = [30, 90, 180, 365];
const DEFAULT_WINDOW = 90;

export const load: PageServerLoad = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');

	const requested = Number(url.searchParams.get('days'));
	const days = ALLOWED_WINDOWS.includes(requested) ? requested : DEFAULT_WINDOW;
	const version = url.searchParams.get('version');

	if (!can(locals.permissions, locals.role, 'flows:read')) {
		return { insights: null as FlowInsights | null, loadFailed: false, days, flowId: params.uid };
	}

	const since = new Date(Date.now() - days * 86_400_000).toISOString();
	const query = new URLSearchParams({ since });
	if (version) query.set('version', version);

	const result = await fetchJson<FlowInsights>(
		fetch,
		`${apiUrl()}/flows/${params.uid}/insights?${query}`,
		authHeaders(locals.accessToken)
	);
	return {
		// A 404 means the journey has no versions yet, which is an empty state, not a failure —
		// the banner is for the transient failures `fetchJson` retried and gave up on.
		insights: result.ok ? result.data : null,
		loadFailed: !result.ok && result.status !== 404,
		days,
		flowId: params.uid
	};
};
