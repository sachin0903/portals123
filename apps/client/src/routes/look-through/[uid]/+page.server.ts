import { loginPath } from '@graphene/auth';
import { error, redirect } from '@sveltejs/kit';
import type { FundLookthroughData } from '@graphene/ui/book/lookthrough';
import { apiUrl, authHeaders } from '$lib/api';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, url, fetch }) => {
	if (!locals.user) redirect(303, loginPath(url));
	const query = new URLSearchParams();
	const asOf = url.searchParams.get('as_of');
	const account = url.searchParams.get('account');
	if (asOf) query.set('as_of', asOf);
	if (account) query.set('account', account);
	const res = await fetch(
		`${apiUrl()}/crm/look-through/${encodeURIComponent(params.uid)}${query.size ? `?${query}` : ''}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (res.status === 404) error(404, 'Fund look-through not found');
	if (!res.ok) error(res.status, 'Could not load fund look-through');
	const lookthrough = (await res.json()) as FundLookthroughData;
	// The way back (ClientShell `back`): the account this holding was opened from, which only this
	// load knows — the route table in $lib/nav would send it home.
	const holding = lookthrough.holding;
	return {
		lookthrough,
		back: holding
			? { href: `/accounts/${holding.account_uid}`, label: holding.account_name ?? 'Account' }
			: undefined
	};
};
