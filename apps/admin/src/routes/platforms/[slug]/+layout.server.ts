import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import { mapLiveOverview, type ApiOverview } from '$lib/platforms/live';
import type { PlatformDataset } from '$lib/platforms/types';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ locals, params, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// Gates the whole /platforms/[slug] tree (overview, models, runs, query, lineage, ingestion):
	// the nav hides it without platforms:read, but every page here must refuse a direct URL too.
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');

	// THE ROUTE GUARD IS THE API's. `params.slug` goes straight through — `_known_provider`
	// resolves either the provider key or its URL slug and 404s an unknown one. It used to be
	// `isPlatformSlug`, a lookup in a module of checked-in FIXTURE data, so which platform pages
	// existed was decided by a TypeScript mock rather than by the registry the API serves.
	const res = await fetch(`${apiUrl()}/admin/platforms/${params.slug}/overview`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404) error(404, 'Unknown platform');
	if (!res.ok) {
		// No fixture to fall back to any more, and that is the point: the page now renders its
		// chrome plus a stated failure, where before it silently swapped in invented models, runs
		// and PRNG-generated client names under a single banner that was easy to miss.
		return { slug: params.slug, dataset: null, liveError: await detail(res) };
	}
	const dataset: PlatformDataset = mapLiveOverview((await res.json()) as ApiOverview);
	return { slug: params.slug, dataset, liveError: null };
};
