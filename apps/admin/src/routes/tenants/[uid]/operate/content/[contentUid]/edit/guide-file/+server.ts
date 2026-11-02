import { targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';
export const GET: RequestHandler = ({ locals, fetch, params }) =>
	targetRequest(fetch, locals.accessToken, params.uid, `/content/items/${params.contentUid}/file`);
