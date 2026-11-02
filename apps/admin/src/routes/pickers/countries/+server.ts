import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Process-lifetime cache for `ref.countries`: the list does not change while a server process
 *  runs, and every country picker would otherwise pay the same round trip. */
let countriesCache: { body: unknown } | null = null;

/** Test hook: the cache lives as long as the process. */
export function _resetCountriesCache(): void {
	countriesCache = null;
}

/** Same-origin countries picker. The token stays on the server; the browser asks this when a
 *  country picker opens. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (countriesCache) return json(countriesCache.body);
	let res: Response;
	try {
		res = await fetch(`${apiUrl()}/ref/countries`, { headers: authHeaders(locals.accessToken) });
	} catch {
		return json({ error: 'The service is unavailable.' }, { status: 503 });
	}
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const body: unknown = await res.json();
	countriesCache = { body };
	return json(body);
};
