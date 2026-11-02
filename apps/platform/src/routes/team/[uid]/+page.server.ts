import { readSignInClient } from '@graphene/auth';
import { apiUrl, authHeaders } from '$lib/api';
import {
	loadMemberProfile,
	revokeMemberSession,
	runMemberAction,
	updateMemberProfile,
	updateAdviserProfile,
	setMemberProviderRef
} from '$lib/server/member-profile';
import type { Actions, PageServerLoad } from './$types';

export type ProviderOrgOption = { value: string; label: string; hint?: string };

const emptyOrgs = { seccl: [] as ProviderOrgOption[], sei: [] as ProviderOrgOption[] };

export const load: PageServerLoad = async ({
	locals,
	fetch,
	params,
	request,
	getClientAddress
}) => {
	const page = await loadMemberProfile(
		locals,
		fetch,
		params.uid,
		'team',
		readSignInClient(request, getClientAddress)
	);
	const orgs = await fetch(`${apiUrl()}/firm/provider-orgs`, {
		headers: authHeaders(locals.accessToken)
	});
	const providerOrgs = orgs.ok
		? ((await orgs.json()) as { seccl: ProviderOrgOption[]; sei: ProviderOrgOption[] })
		: emptyOrgs;
	return { ...page, providerOrgs };
};

export const actions: Actions = {
	passwordReset: async (event) => runMemberAction(event, 'passwordReset', 'platform'),
	revokeInvitation: async (event) => runMemberAction(event, 'revokeInvitation', 'platform'),
	resetMfa: async (event) => runMemberAction(event, 'resetMfa', 'platform'),
	deactivate: async (event) => runMemberAction(event, 'deactivate', 'platform'),
	reactivate: async (event) => runMemberAction(event, 'reactivate', 'platform'),
	updateUser: async (event) => updateMemberProfile(event),
	setAdviser: async (event) => updateAdviserProfile(event),
	revokeSession: async (event) => revokeMemberSession(event),
	setProviderRef: async (event) => setMemberProviderRef(event)
};
