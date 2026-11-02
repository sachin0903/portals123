import { error, redirect } from '@sveltejs/kit';
import type { FundLookthroughData } from '@graphene/ui/book/lookthrough';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, url, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'lookthrough:read')) error(403, 'Not authorised');
	const query = new URLSearchParams();
	const asOf = url.searchParams.get('as_of');
	if (asOf) query.set('as_of', asOf);
	const res = await fetch(
		`${apiUrl()}/admin/look-through/${encodeURIComponent(params.uid)}${query.size ? `?${query}` : ''}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (res.status === 404) error(404, 'Fund look-through not found');
	if (!res.ok) error(res.status, 'Could not load fund look-through');
	return { lookthrough: (await res.json()) as FundLookthroughData };
};
