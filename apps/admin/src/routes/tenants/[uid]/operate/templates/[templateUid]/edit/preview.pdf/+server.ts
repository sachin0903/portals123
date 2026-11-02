import { json } from '@sveltejs/kit';
import { canOperateWrite, targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';
export const POST: RequestHandler = async ({ request, fetch, locals, params }) => {
	if (!canOperateWrite(locals)) return json({ error: 'Read-only' }, { status: 403 });
	return targetRequest(
		fetch,
		locals.accessToken,
		params.uid,
		`/templates/${params.templateUid}/preview.pdf`,
		{ method: 'POST', headers: { 'content-type': 'application/json' }, body: await request.text() }
	);
};
