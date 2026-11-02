import { targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';
export const GET: RequestHandler = ({ url, fetch, locals, params }) =>
	targetRequest(
		fetch,
		locals.accessToken,
		params.uid,
		`/datasets/${url.searchParams.get('uid') ?? ''}/data`,
		{},
		Object.fromEntries(url.searchParams)
	);
