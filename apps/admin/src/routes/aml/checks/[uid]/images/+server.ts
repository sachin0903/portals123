import { relayJson } from '$lib/server/aml';
import type { RequestHandler } from './$types';

// The photos one identity check holds: browser -> this route -> API `/aml/checks/{uid}/images`,
// which records the view. `?record=false` lists them without recording, for photos read ahead of
// a profile not yet on screen (`viewed` records them once shown). Each photo's bytes come from
// `[part]`.
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	const record = url.searchParams.get('record') !== 'false';
	const path = `/aml/checks/${encodeURIComponent(params.uid)}/images`;
	return relayJson({ locals, fetch }, record ? path : `${path}?record=false`);
};
