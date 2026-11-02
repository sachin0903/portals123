/**
 * Where the phone link lands.
 *
 * The code in the path works once. It is exchanged here for the session token and replaced by
 * a redirect to `/i`, which holds no code. A code that cannot be spent renders this page: the
 * link has already been used, or it has expired.
 */

import { redirect } from '@sveltejs/kit';
import { env } from '$env/dynamic/private';
import { redeemPhoneLink } from '$lib/server/identity-phone';
import { IDV_PHONE_COOKIE, cookieOptions } from '$lib/server/workos';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, cookies, fetch }) => {
	const redeemed = await redeemPhoneLink(params.code, {
		internalToken: env.INTERNAL_API_TOKEN ?? '',
		fetch
	});
	if (!redeemed) return { ended: true as const };

	cookies.set(IDV_PHONE_COOKIE, redeemed.token, {
		...cookieOptions,
		path: '/i',
		maxAge: redeemed.secondsRemaining
	});
	redirect(303, '/i');
};
