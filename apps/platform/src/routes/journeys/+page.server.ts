import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { AppExecutionRow, FlowOut } from '@graphene/ui/book/journeys';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'cases:read')) {
		error(403, 'You do not have permission to view applications.');
	}
	const canStart = can(locals.permissions, locals.role, 'cases:write');
	// The permission is resolved here and the inbox is STREAMED: the page's shape is on screen
	// on the click. Both refusals stay out here — a 403 and a 303 have to be thrown before the
	// response starts, not from inside a promise.
	return {
		inbox: loadInbox(locals.accessToken, fetch, canStart)
	};
};

async function loadInbox(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	canStart: boolean
) {
	const headers = authHeaders(accessToken);
	const [res, flows] = await Promise.all([
		fetchJson<AppExecutionRow[]>(fetch, `${apiUrl()}/applications`, headers),
		// The start menu only wants published flows, and only exists for someone who can start a
		// journey. Fetching the whole catalogue (drafts included) for a read-only inbox was wasted
		// work; fetching it at all without cases:write was worse.
		canStart ? fetchJson<FlowOut[]>(fetch, `${apiUrl()}/flows?status=published`, headers) : null
	]);
	// The orchestration engine is unconditional (there is no v1 fallback), so a failure here is a
	// real load failure — surface it as one rather than as a fake-empty list.
	return {
		applications: res.ok ? res.data : [],
		loadFailed: !res.ok,
		startFlows: canStart && flows?.ok ? flows.data : []
	};
}
