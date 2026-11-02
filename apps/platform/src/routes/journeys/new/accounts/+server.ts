import { json } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson } from '$lib/api';
import type { IllustrationModel } from '$lib/illustrations/form';
import type { OnboardingIllustration } from '../+page.server';
import type { RequestHandler } from './$types';

/** Accounts-step catalogues: onboarding illustrations and investment models.
 *  Loaded when that step is reached, not on first paint of `/journeys/new`. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (!can(locals.permissions, locals.role, 'cases:write')) {
		return json({ error: 'Not permitted.' }, { status: 403 });
	}
	const h = authHeaders(locals.accessToken);
	const [illustrations, models] = await Promise.all([
		fetchJson<OnboardingIllustration[]>(fetch, `${apiUrl()}/crm/illustrations/onboarding`, h),
		fetchJson<IllustrationModel[]>(fetch, `${apiUrl()}/crm/models`, h)
	]);
	return json({
		illustrations: illustrations.ok ? illustrations.data : [],
		models: models.ok ? models.data : []
	});
};
