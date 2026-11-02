import { error, redirect } from '@sveltejs/kit';
import type { FundLookthroughData } from '@graphene/ui/book/lookthrough';
import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, url, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const query = new URLSearchParams();
	const asOf = url.searchParams.get('as_of');
	const account = url.searchParams.get('account');
	if (asOf) query.set('as_of', asOf);
	if (account) query.set('account', account);
	return {
		record: loadRecord(locals.accessToken, fetch, params.uid, query)
	};
};

async function loadRecord(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	query: URLSearchParams
) {
	const res = await fetch(
		`${apiUrl()}/crm/look-through/${encodeURIComponent(uid)}${query.size ? `?${query}` : ''}`,
		{ headers: authHeaders(accessToken) }
	);
	if (res.status === 404) error(404, 'Fund look-through not found');
	if (!res.ok) error(res.status, 'Could not load fund look-through');
	return { lookthrough: (await res.json()) as FundLookthroughData };
}
