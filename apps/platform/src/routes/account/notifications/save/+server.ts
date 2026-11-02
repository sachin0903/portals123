import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Persist the caller's notification preferences (user-keyed on the API). Preferences are per
 * NOTIFICATION, so each row carries its `trigger_key` — the API keys the override on
 * (category, trigger_key) and skips any row without it. */
export const POST: RequestHandler = async ({ locals, request, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	const body = (await request.json()) as {
		preferences: {
			category: string;
			trigger_key?: string | null;
			in_app: boolean;
			email: boolean;
		}[];
	};
	const res = await fetch(`${apiUrl()}/notifications/preferences`, {
		method: 'PUT',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return json({ error: 'Could not save preferences.' }, { status: res.status });
	return json(await res.json());
};
