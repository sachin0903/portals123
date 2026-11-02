import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { Client, ManualIdentityDocument } from '../+page.server';
import { signedInIsAdviser } from '../kickoff';
import type { RequestHandler } from './$types';

type Member = { user_id: string; name: string | null; email: string | null; is_adviser: boolean };

/** Applicants-step catalogues: existing clients, advisers, and the IDV route.
 *  Loaded when that step mounts, not on first paint of `/journeys/new`. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'cases:write')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const h = authHeaders(locals.accessToken);
	const [clients, idv, members] = await Promise.all([
		fetchJson<Client[]>(fetch, `${apiUrl()}/documents/clients`, h),
		fetchJson<{ available: boolean; manual_documents?: ManualIdentityDocument[] }>(
			fetch,
			`${apiUrl()}/ref/idv`,
			h
		),
		fetchJson<Member[]>(fetch, `${apiUrl()}/firm/members?kind=staff`, h)
	]);
	const advisers = members.ok
		? (Array.isArray(members.data) ? members.data : []).filter((m) => m.is_adviser === true)
		: [];
	return json({
		clients: clients.ok ? clients.data : [],
		advisers,
		// The blank picker's last fallback is the signed-in user, and only when they are an
		// adviser. The wizard needs to know that to ask for a choice before the API refuses one.
		signedInIsAdviser: signedInIsAdviser(locals.user.email, advisers),
		digitalIdvAvailable: idv.ok ? idv.data.available === true : false,
		manualIdentityDocuments: idv.ok ? (idv.data.manual_documents ?? []) : []
	});
};
