import { json, type RequestHandler } from '@sveltejs/kit';
import { privateSseResponse } from '@graphene/auth';
import { apiUrl } from '$lib/api';

export const GET: RequestHandler = ({ locals, fetch, request }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	return privateSseResponse({
		fetch,
		apiBaseUrl: apiUrl(),
		accessToken: locals.accessToken,
		ticketPath: '/messaging/ticket',
		eventsPath: '/messaging/events',
		signal: request.signal
	});
};
