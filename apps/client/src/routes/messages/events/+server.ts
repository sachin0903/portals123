import { json, type RequestHandler } from '@sveltejs/kit';
import { privateSseResponse } from '@graphene/auth';
import { contactIdQuery } from '$lib/active-contact';
import { apiUrl } from '$lib/api';

export const GET: RequestHandler = ({ locals, fetch, request, url }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const ticketPath = `/messaging/ticket${contactIdQuery(url.searchParams.get('contact_id'))}`;
	return privateSseResponse({
		fetch,
		apiBaseUrl: apiUrl(),
		accessToken: locals.accessToken,
		ticketPath,
		eventsPath: '/messaging/events',
		signal: request.signal
	});
};
