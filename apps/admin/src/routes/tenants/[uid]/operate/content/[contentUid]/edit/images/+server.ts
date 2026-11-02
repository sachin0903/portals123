import { json } from '@sveltejs/kit';
import { canOperateWrite, targetRequest } from '$lib/operate';
import type { RequestHandler } from './$types';
export const POST: RequestHandler = async ({ request, locals, fetch, params }) => {
	if (!canOperateWrite(locals)) return json({ error: 'Read-only' }, { status: 403 });
	const input = await request.formData(),
		out = new FormData();
	const file = input.get('file');
	if (!(file instanceof File)) return json({ error: 'Choose an image.' }, { status: 422 });
	out.set('file', file, file.name);
	return targetRequest(
		fetch,
		locals.accessToken,
		params.uid,
		`/content/items/${params.contentUid}/images`,
		{ method: 'POST', body: out }
	);
};
