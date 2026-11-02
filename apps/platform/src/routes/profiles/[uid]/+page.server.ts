import { readSignInClient } from '@graphene/auth';
import {
	loadMemberProfile,
	revokeMemberSession,
	runMemberAction
} from '$lib/server/member-profile';
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = ({ locals, fetch, params, request, getClientAddress }) =>
	loadMemberProfile(
		locals,
		fetch,
		params.uid,
		'profiles',
		readSignInClient(request, getClientAddress)
	);

export const actions: Actions = {
	passwordReset: async (event) => runMemberAction(event, 'passwordReset', 'client'),
	revokeInvitation: async (event) => runMemberAction(event, 'revokeInvitation', 'client'),
	resetMfa: async (event) => runMemberAction(event, 'resetMfa', 'client'),
	deactivate: async (event) => runMemberAction(event, 'deactivate', 'client'),
	reactivate: async (event) => runMemberAction(event, 'reactivate', 'client'),
	revokeSession: async (event) => revokeMemberSession(event)
};
