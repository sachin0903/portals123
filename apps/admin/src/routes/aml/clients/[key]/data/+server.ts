import { relayJson } from '$lib/server/aml';
import type { RequestHandler } from './$types';

// One client's page body for the workbench, which moves between profiles without a reload:
// browser -> this route -> API `/aml/clients/{key}`. `?images=false` reads it without listing the
// identity photos, so nothing is recorded: the read ahead for a profile not yet on screen.
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	const photos = url.searchParams.get('images') !== 'false';
	const path = `/aml/clients/${encodeURIComponent(params.key)}${photos ? '' : '?images=false'}`;
	return relayJson({ locals, fetch }, path);
};
