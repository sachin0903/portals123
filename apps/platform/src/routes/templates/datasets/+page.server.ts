import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import { toDataset, type ApiDataset } from '$lib/datasets';
import type { PageServerLoad } from './$types';

/**
 * The datasets LIST, on its own route.
 *
 * It used to be a third segment of `/templates`'s view switch, reached as `?kind=datasets`. A
 * dataset is not a third kind of template, though — it is an input a document template draws on,
 * and an email uses none — so sitting it beside Documents and Emails said the wrong thing about
 * what it is. It is reached from a button on `/templates` now, and `templates/datasets/{new,[id]}`
 * were already real routes beneath this one, so this is the list those two always implied.
 *
 * Its own load, not the parent's: `/templates` fetches datasets for the create wizard's dataset
 * step and would otherwise be paying for a list nobody on that page is looking at.
 */
export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'datasets:read')) error(403, 'Not permitted');

	// The search lives in the URL so the API answers it — across every dataset, not across the ones
	// already fetched — and so it survives a reload and can be linked. It came with the listing when
	// datasets moved under `/templates`; without it here the toolbar would still navigate and this
	// loader would ignore it, which reads as a search that silently does nothing.
	const query = url.searchParams.get('q')?.trim() ?? '';
	const headers = authHeaders(locals.accessToken);
	const res = await fetchJson<ApiDataset[]>(
		fetch,
		`${apiUrl()}/datasets?${new URLSearchParams(query ? { q: query } : {})}`,
		headers
	);

	return {
		query,
		datasets: res.ok ? res.data.map(toDataset) : [],
		// The listing shows its own banner rather than the page failing: a dataset list that cannot
		// load is a retry, not a dead end.
		loadError: !res.ok,
		canManageDatasets: can(locals.permissions, locals.role, 'datasets:write')
	};
};
