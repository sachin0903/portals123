import { requestPasswordReset, safeClientAddress } from '@graphene/auth';
import { env } from '$env/dynamic/private';
import type { Actions } from './$types';

export const actions: Actions = {
	default: async ({ request, url, fetch, getClientAddress }) => {
		const data = await request.formData();
		const email = String(data.get('email') ?? '').trim();
		// Best-effort + always the same result, so we never reveal whether the account exists.
		await requestPasswordReset(email, url.host, {
			apiUrl: env.API_URL ?? '',
			internalToken: env.INTERNAL_API_TOKEN ?? '',
			// The end user's address, so the API's per-IP cap on reset sends is keyed on the person
			// asking rather than on the portal container it can see.
			clientIp: safeClientAddress(getClientAddress),
			fetch
		});
		return { sent: true, email };
	}
};
