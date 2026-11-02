import { relayJson } from '$lib/server/aml';
import type { RequestHandler } from './$types';

// Record that the photos the workbench read ahead (`?record=false`) were shown: browser -> this
// route -> API `/aml/checks/{uid}/images/viewed` with `{image_count}`, the same "identity images
// viewed" a listing records.
export const POST: RequestHandler = async ({ params, request, locals, fetch }) => {
	const body: unknown = await request.json().catch(() => undefined);
	return relayJson(
		{ locals, fetch },
		`/aml/checks/${encodeURIComponent(params.uid)}/images/viewed`,
		{ json: body }
	);
};
