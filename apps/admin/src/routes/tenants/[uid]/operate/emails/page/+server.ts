import { targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';
const KEYS = [
	'limit',
	'before',
	'after',
	'status',
	'template_code',
	'category',
	'event_type',
	'q',
	'since',
	'until',
	'sort',
	'dir',
	'offset'
] as const;
export const GET: RequestHandler = ({ url, locals, fetch, params }) => {
	const q = new URLSearchParams();
	for (const k of KEYS) {
		const v = url.searchParams.get(k);
		if (v) q.set(k, v);
	}
	return targetRequest(fetch, locals.accessToken, params.uid, '/firm/comms/messages', {}, q);
};
