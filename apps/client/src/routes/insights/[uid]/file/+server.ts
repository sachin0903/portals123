import { RelayTooLarge, relayBuffered } from '@graphene/auth';
import { error } from '@sveltejs/kit';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl, authHeaders, publicId } from '$lib/api';
import { ACTIVE_CONTACT_COOKIE } from '$lib/server/workos';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ params, locals, fetch, cookies, url }) => {
	if (!locals.user) error(401, 'Not signed in');
	const uid = publicId(params.uid);
	if (!uid) error(404, 'Guide unavailable');
	const qs = contactIdQuery(
		url.searchParams.get('contact_id') ?? cookies.get(ACTIVE_CONTACT_COOKIE)
	);
	const res = await fetch(`${apiUrl()}/crm/content/${uid}/file${qs}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Guide unavailable');
	try {
		return await relayBuffered(res, {
			fallbackDisposition: 'inline',
			fallbackType: 'application/pdf'
		});
	} catch (e) {
		if (e instanceof RelayTooLarge) error(502, 'Guide unavailable');
		throw e;
	}
};
