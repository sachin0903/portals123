import { json } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** Process-lifetime cache for `ref.countries` — the vocabulary does not change during a server
 *  process, and every nationality / address picker would otherwise pay the same round trip. */
let countriesCache: { body: unknown } | null = null;

/** Test hook: the cache is process-lifetime, so a suite that hits this route more than once
 *  would otherwise see the first fixture forever. */
export function _resetCountriesCache(): void {
	countriesCache = null;
}

/** Same-origin countries picker. Token stays server-side; the browser fetches this on picker open. */
export const GET: RequestHandler = async ({ locals, fetch }) => {
	if (!locals.user) return json({ error: 'Not signed in.' }, { status: 401 });
	if (countriesCache) return json(countriesCache.body);
	const res = await fetch(`${apiUrl()}/ref/countries`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) return json({ error: await detail(res) }, { status: res.status });
	const body: unknown = await res.json();
	countriesCache = { body };
	return json(body);
};
