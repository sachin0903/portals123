import { error, redirect } from '@sveltejs/kit';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { PageServerLoad } from './$types';

export type UserRow = {
	/** `auth.users.id` — the uuid the member endpoints are typed on. Never a URL. */
	user_id: string;
	/** `auth.users.uid` — what the record link is addressed by. */
	user_uid: string;
	email: string;
	name: string | null;
	enabled: boolean;
	workos_user_id: string | null;
	tenant_id: number | null;
	/** The firm's uid — what the admin tenant routes are addressed by. */
	tenant_uid: string | null;
	tenant_name: string | null;
	tenant_alias: string | null;
	memberships: number;
};

/** One page of the directory. Client logins accumulate without limit, so paging + search are the
 *  API's job (`?q=&limit=&offset=`) — the browser never holds the whole estate. The page size is
 *  the product-wide listing one (`@graphene/ui/book/listing`). */

export const load: PageServerLoad = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'tenants:read')) error(403, 'Not authorised');

	const q = url.searchParams.get('q')?.trim() ?? '';
	// `active` / `disabled` in the URL, `enabled=true|false` on the wire. The URL word is what a
	// person reads in the address bar and what the badge in the row says; the boolean is what the
	// column is. Anything else is treated as unset rather than as a third state.
	const raw = url.searchParams.get('status')?.trim() ?? '';
	const status = raw === 'active' || raw === 'disabled' ? raw : '';
	const offset = Math.max(0, Number(url.searchParams.get('offset') ?? 0) || 0);
	const query = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
	if (q) query.set('q', q);
	// Server-side, not over the rows on screen: this endpoint deliberately never ships the whole
	// estate to the browser, so narrowing the 25 logins in hand would answer "two disabled
	// accounts" when the true answer is however many the estate holds.
	if (status) query.set('enabled', String(status === 'active'));

	let users: UserRow[] = [];
	let total = 0;
	let apiError: string | null = null;
	try {
		const res = await fetch(`${apiUrl()}/admin/users?${query}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (res.ok) {
			const body = await res.json();
			users = body.users ?? [];
			total = body.total ?? users.length;
		} else if (res.status === 403)
			apiError = 'Your account needs a Graphene system/admin role to view users.';
		else apiError = await detail(res);
	} catch {
		apiError = 'The service is unavailable.';
	}

	return { users, total, q, status, offset, pageSize: PAGE_SIZE, apiError };
};
