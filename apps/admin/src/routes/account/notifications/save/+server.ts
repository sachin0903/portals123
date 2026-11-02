import { json, type RequestHandler } from '@sveltejs/kit';
import { apiUrl, jsonHeaders } from '$lib/api';

/** Persist the operator's notification preferences, keyed on (category, trigger_key). */
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
