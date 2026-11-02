import { requestPasswordReset, safeClientAddress } from '@graphene/auth';
import { env } from '$env/dynamic/private';
import type { Actions } from './$types';

export const actions: Actions = {
	default: async ({ request, url, fetch, getClientAddress }) => {
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		// Best-effort + always the same result, so we never reveal whether the account exists. The
		// caller's IP goes with it: the API's per-IP cap on this surface is keyed on it, and the API
		// only ever sees the portal container otherwise.
		await requestPasswordReset(email, url.host, {
			apiUrl: env.API_URL ?? '',
			internalToken: env.INTERNAL_API_TOKEN ?? '',
			clientIp: safeClientAddress(getClientAddress),
			fetch
		});
		return { sent: true, email };
	}
};
