import { json, type RequestHandler } from '@sveltejs/kit';
import { privateSseResponse } from '@graphene/auth';
import { env } from '$env/dynamic/private';

export const GET: RequestHandler = ({ locals, fetch, request, params }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const target = new URLSearchParams({ tenant: params.uid! });
	return privateSseResponse({
		fetch,
		apiBaseUrl: env.API_URL ?? 'http://127.0.0.1:8000',
		accessToken: locals.accessToken,
		ticketPath: `/messaging/ticket?${target}`,
		eventsPath: '/messaging/events',
		signal: request.signal
	});
};
