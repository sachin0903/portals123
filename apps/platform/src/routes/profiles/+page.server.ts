import { fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, jsonHeaders, detail, type Member, type OrgRole } from '$lib/api';
import { can } from '@graphene/api-client/permissions';
import type { Actions, PageServerLoad } from './$types';

/** Everything the page renders, as one streamed object. */
type Directory = {
	profiles: Member[];
	roles: OrgRole[];
	/** The API refused the read (403) — distinct from a firm with no profiles yet. */
	forbidden: boolean;
	/** How many profiles match ACROSS the firm — one per portal-enabled client, so this is the
	 *  directory that genuinely grows. */
	total: number;
};

type DateRange = [string, string];

/** One window of profiles. The window is not the point; the SEARCH reaching past it is. */
const PAGE_SIZE = 100;

export const load: PageServerLoad = async ({ locals, fetch, url }) => {
	if (!locals.user) redirect(303, '/login');

	// STREAMED, like the book surfaces: awaiting the directory here left the previous page on screen
	// for the whole round trip with Profiles already lit in the sidebar. The create form's client
	// picker is typeahead (`/profiles/clients`) — it must not ride this first paint.
	const authorised =
		can(locals.permissions, locals.role, 'profiles:read') ||
		can(locals.permissions, locals.role, 'members:read');
	const canManage =
		can(locals.permissions, locals.role, 'profiles:write') ||
		can(locals.permissions, locals.role, 'members:write');
	const canCreateExisting = canManage && can(locals.permissions, locals.role, 'contacts:write');
	const canCreateNew = canManage && can(locals.permissions, locals.role, 'contacts:create');
	// The search and every filter below are the URL's, not the loader's own window.
	const query = url.searchParams.get('q')?.trim() ?? '';
	// Named `roleFilter`: a plain `role` here would shadow the root layout's global `role`
	// (the caller's own role slug), as it did until svelte-check caught it.
	const roleFilter = url.searchParams
		.getAll('role')
		.map((r) => r.trim())
		.filter(Boolean);
	const memberStatus = url.searchParams
		.getAll('status')
		.map((s) => s.trim())
		.filter(Boolean);
	const memberSince: DateRange = [
		url.searchParams.get('since')?.trim() ?? '',
		url.searchParams.get('until')?.trim() ?? ''
	];
	const lastActive: DateRange = [
		url.searchParams.get('active_since')?.trim() ?? '',
		url.searchParams.get('active_until')?.trim() ?? ''
	];
	return {
		authorised,
		query,
		roleFilter,
		status: memberStatus,
		memberSince,
		lastActive,
		canManage,
		canCreate: canCreateExisting || canCreateNew,
		canCreateExisting,
		canCreateNew,
		directory: authorised
			? loadDirectory(
					locals.accessToken,
					fetch,
					query,
					roleFilter,
					memberStatus,
					memberSince,
					lastActive
				)
			: Promise.resolve<Directory>({
					profiles: [],
					total: 0,
					roles: [],
					forbidden: true
				})
	};
};

async function loadDirectory(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	query: string,
	role: string[],
	memberStatus: string[],
	memberSince: DateRange,
	lastActive: DateRange
): Promise<Directory> {
	const headers = authHeaders(accessToken);
	const search = new URLSearchParams({
		kind: 'profile',
		limit: String(PAGE_SIZE),
		offset: '0'
	});
	if (query) search.set('q', query);
	for (const r of role) search.append('role', r);
	for (const s of memberStatus) search.append('status', s);
	if (memberSince[0]) search.set('since', memberSince[0]);
	if (memberSince[1]) search.set('until', memberSince[1]);
	if (lastActive[0]) search.set('active_since', lastActive[0]);
	if (lastActive[1]) search.set('active_until', lastActive[1]);
	const [profilesRes, rolesRes] = await Promise.all([
		fetch(`${apiUrl()}/firm/members?${search}`, { headers }),
		fetch(`${apiUrl()}/firm/roles`, { headers })
	]);
	if (profilesRes.status === 403) return { profiles: [], total: 0, roles: [], forbidden: true };

	const page: { items: Member[]; total: number } = profilesRes.ok
		? await profilesRes.json()
		: { items: [], total: 0 };
	return {
		profiles: page.items,
		total: page.total,
		// Only client-portal roles: a profile may never hold a staff role (0119's boundary).
		roles: rolesRes.ok
			? ((await rolesRes.json()) as OrgRole[]).filter((role) => role.portal === 'client')
			: [],
		forbidden: false
	};
}

function requireManage(locals: App.Locals): void {
	if (!locals.user) redirect(303, '/login');
	if (
		!can(locals.permissions, locals.role, 'profiles:write') &&
		!can(locals.permissions, locals.role, 'members:write')
	)
		redirect(303, '/profiles');
}

export const actions: Actions = {
	addProfile: async ({ request, locals, fetch }) => {
		requireManage(locals);
		if (!can(locals.permissions, locals.role, 'contacts:write')) redirect(303, '/profiles');
		const data = await request.formData();
		const contactUid = String(data.get('contact_uid') ?? '').trim();
		const roleSlugs = data.getAll('role_slugs').map(String).filter(Boolean);
		if (!contactUid || roleSlugs.length === 0) {
			return fail(400, {
				action: 'addProfile',
				error: 'A client and at least one role are required.'
			});
		}
		const res = await fetch(`${apiUrl()}/firm/contacts/${contactUid}/portal-invite`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ role_slugs: roleSlugs })
		});
		if (!res.ok) return fail(res.status, { action: 'addProfile', error: await detail(res) });
		const body = await res.json().catch(() => ({}));
		return {
			action: 'addProfile',
			ok: true,
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	addNewProfile: async ({ request, locals, fetch }) => {
		requireManage(locals);
		if (!can(locals.permissions, locals.role, 'contacts:create')) redirect(303, '/profiles');
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		const firstName = String(data.get('first_name') ?? '').trim();
		const lastName = String(data.get('last_name') ?? '').trim();
		const roleSlugs = data.getAll('role_slugs').map(String).filter(Boolean);
		if (!email || !firstName || !lastName || roleSlugs.length === 0) {
			return fail(400, {
				action: 'addNewProfile',
				error: 'First name, last name, email and at least one role are required.'
			});
		}
		const res = await fetch(`${apiUrl()}/firm/members`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				email,
				first_name: firstName,
				last_name: lastName,
				role_slugs: roleSlugs
			})
		});
		if (!res.ok) return fail(res.status, { action: 'addNewProfile', error: await detail(res) });
		const body = await res.json().catch(() => ({}));
		return {
			action: 'addNewProfile',
			ok: true,
			emailDelivered: Boolean(body?.email_delivered)
		};
	},

	setRoles: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const data = await request.formData();
		const userId = String(data.get('user_id') ?? '');
		const roleSlugs = data.getAll('role_slugs').map(String).filter(Boolean);
		if (!userId || roleSlugs.length === 0) {
			return fail(400, {
				action: 'setRoles',
				error: 'A profile and at least one role are required.'
			});
		}
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/portals/client`, {
			method: 'PATCH',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ role_slugs: roleSlugs })
		});
		if (!res.ok) return fail(res.status, { action: 'setRoles', error: await detail(res) });
		return { action: 'setRoles', ok: true, userId };
	},

	deactivate: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const userId = String((await request.formData()).get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'deactivate', error: 'A profile is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/deactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'deactivate', error: await detail(res) });
		return { action: 'deactivate', ok: true, userId };
	},

	reactivate: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const userId = String((await request.formData()).get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'reactivate', error: 'A profile is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/reactivate`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'reactivate', error: await detail(res) });
		return { action: 'reactivate', ok: true, userId };
	},

	resendInvite: async ({ request, locals, fetch }) => {
		requireManage(locals);
		const userId = String((await request.formData()).get('user_id') ?? '');
		if (!userId) return fail(400, { action: 'resendInvite', error: 'A profile is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/resend-invite`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'resendInvite', error: await detail(res) });
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
		if (!userId) return fail(400, { action: 'revokeInvite', error: 'A profile is required.' });
		const res = await fetch(`${apiUrl()}/firm/members/${userId}/invitation`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) return fail(res.status, { action: 'revokeInvite', error: await detail(res) });
		return { action: 'revokeInvite', ok: true, userId };
	}
};
