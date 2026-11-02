import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type Member, type OrgRole } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

/** One window of the directory. A firm's staff fits in this comfortably; the point of the window
 *  is that the SEARCH behind it reaches past it. */
const PAGE_SIZE = 100;

/** The directory the page renders, as one streamed object. */
type Team = {
	members: Member[];
	/** How many staff match ACROSS the firm — what makes "showing 100 of 240" honest. */
	total: number;
	roles: OrgRole[];
	/** The API refused the read (403) — distinct from a firm with no staff. */
	forbidden: boolean;
};

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// The permission is resolved SYNCHRONOUSLY and the directory is STREAMED, matching the book
	// surfaces (see clients/+page.server.ts): the page renders on the click and fills in when the API
	// answers, rather than leaving the PREVIOUS page on screen with Team already lit in the sidebar —
	// which is exactly what "the Team page doesn't load" looked like.
	const authorised = can(locals.permissions, locals.role, 'members:read');
	// The search and the role filter live in the URL, so the API answers them and both survive a
	// reload. Filtering in the browser searched whatever the loader had fetched and reported it as
	// the firm's answer.
	const query = url.searchParams.get('q')?.trim() ?? '';
	// Named `roleFilter`: a plain `role` would shadow the root layout's global `role`.
	const roleFilter = url.searchParams
		.getAll('role')
		.map((r) => r.trim())
		.filter(Boolean);
	return {
		authorised,
		query,
		roleFilter,
		canManage: can(locals.permissions, locals.role, 'members:write'),
		team: authorised
			? loadTeam(locals.accessToken, fetch, query, roleFilter)
			: Promise.resolve<Team>({ members: [], total: 0, roles: [], forbidden: true })
	};
};

async function loadTeam(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: string,
	role: string[]
): Promise<Team> {
	const headers = authHeaders(accessToken);
	const search = new URLSearchParams({
		kind: 'staff',
		limit: String(PAGE_SIZE),
		offset: '0'
	});
	if (query) search.set('q', query);
	for (const r of role) search.append('role', r);
	const [membersRes, rolesRes] = await Promise.all([
		fetch(`${apiUrl()}/firm/members?${search}`, { headers }),
		fetch(`${apiUrl()}/firm/roles`, { headers })
	]);
	// The API also enforces members:read (403); treat that as not-authorised too.
	if (membersRes.status === 403) return { members: [], total: 0, roles: [], forbidden: true };
	const page: { items: Member[]; total: number } = membersRes.ok
		? await membersRes.json()
		: { items: [], total: 0 };
	return {
		members: page.items,
		total: page.total,
		roles: rolesRes.ok ? await rolesRes.json() : [],
		forbidden: false
	};
}

/** Reject unless the caller can manage members; keeps every action gated in one place. */
function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'members:write')) redirect(303, '/team');
}

export const actions: Actions = {
	addMember: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		const roleSlugs = data.getAll('role_slugs').map(String).filter(Boolean);
		if (!email || roleSlugs.length === 0)
			return fail(400, { action: 'addMember', error: 'A valid email and role are required.' });
		const res = await fetch(`${apiUrl()}/firm/members`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				email,
				role_slugs: roleSlugs,
				first_name: String(data.get('first_name') ?? '').trim() || null,
				last_name: String(data.get('last_name') ?? '').trim() || null
			})
		});
		if (!res.ok) return fail(res.status, { action: 'addMember', error: await detail(res) });
		// The set-password link is a LIVE CREDENTIAL: anyone holding it can set this person's password
		// and sign in as them. It is deliberately NOT read out of the response here — rendering it put
		// it in the page HTML, the SvelteKit action payload, and every proxy log and browser history
		// in between. The API already withholds it unless `reveal_link` is set (which this endpoint
		// does not ask for), so what we report instead is whether the invitation email actually left:
		// `invitation_sent` (a link was minted) and `email_delivered` (Resend accepted it).
		const body = await res.json().catch(() => ({}));
		return {
			action: 'addMember',
			ok: true,
			invitedEmail: email,
			invitationSent: Boolean(body?.invitation_sent),
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	setRoles: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const roleSlugs = data.getAll('role_slugs').map(String).filter(Boolean);
		if (!userId || roleSlugs.length === 0)
			return fail(400, {
				action: 'setRoles',
				error: 'A member and at least one role are required.'
			});
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/portals/platform`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ role_slugs: roleSlugs })
		});
		if (!res.ok) return fail(res.status, { action: 'setRoles', error: await detail(res) });
		return { action: 'setRoles', ok: true, userId };
	},

	deactivate: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'deactivate', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/deactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'deactivate', error: await detail(res) });
		return { action: 'deactivate', ok: true, userId };
	},

	reactivate: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'reactivate', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/reactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'reactivate', error: await detail(res) });
		return { action: 'reactivate', ok: true, userId };
	},

	resendInvite: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'resendInvite', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/resend-invite`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'resendInvite', error: await detail(res) });
		// Same as addMember: report whether the email was delivered rather than handing back the link.
		// A resend that Resend refused must not toast "Invitation resent".
		const body = await res.json().catch(() => ({}));
		return {
			action: 'resendInvite',
			ok: true,
			userId,
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	revokeInvite: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const userId = String((await request.formData()).get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'revokeInvite', error: 'A user is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/invitation`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'revokeInvite', error: await detail(res) });
		return { action: 'revokeInvite', ok: true, userId };
	}
};
