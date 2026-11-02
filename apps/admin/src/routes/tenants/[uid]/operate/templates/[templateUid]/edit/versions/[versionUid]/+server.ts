import { targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';
export const GET: RequestHandler = ({ fetch, locals, params }) =>
	targetRequest(
		fetch,
		locals.accessToken,
		params.uid,
		`/templates/${params.templateUid}/versions/${params.versionUid}`
	);
