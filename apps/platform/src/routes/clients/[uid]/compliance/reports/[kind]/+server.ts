import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** The compliance reports the API gives a firm as a PDF. */
const KINDS = ['client', 'identity', 'aml'] as const;

// One compliance report on a client as a PDF download, through a SAME-ORIGIN relay: the browser
// never talks to the API or the identity provider directly.
// browser -> this route -> API `/crm/contacts/{uid}/compliance-reports/{kind}`.
// `?check=` names the identity check to report on; without it the API reports on the latest.
export const GET: RequestHandler = async ({ params, locals, fetch, url }) => {
	if (!locals.user) error(401, 'Not signed in');
	if (!can(locals.permissions, locals.role, 'screening:read')) error(403, 'Not authorised');
	if (!(KINDS as readonly string[]).includes(params.kind)) error(404, 'No such report.');

	const check = params.kind === 'identity' ? url.searchParams.get('check') : null;
	const query = check ? `?check=${encodeURIComponent(check)}` : '';
	let res: Response;
	try {
		res = await fetch(
			`${apiUrl()}/crm/contacts/${encodeURIComponent(params.uid)}/compliance-reports/${params.kind}${query}`,
			{ headers: authHeaders(locals.accessToken) }
		);
	} catch {
		error(503, 'The report is unavailable.');
	}
	if (!res.ok) {
		if (res.status === 403 || res.status === 404 || res.status === 409)
			error(res.status, await detail(res));
		// The API could not make the PDF: its reason, which says to try again.
		if (res.status === 503) {
			const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
			error(
				503,
				typeof body?.detail === 'string' ? body.detail : 'The report could not be made. Try again.'
			);
		}
		error(502, 'The report is unavailable.');
	}
	// Buffered, like the photo relay: `new Response(ArrayBuffer)` sets Content-Length itself.
	const body = await res.arrayBuffer();
	return new Response(body, {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/pdf',
			'content-disposition': res.headers.get('content-disposition') ?? 'attachment',
			'cache-control': 'private, no-store'
		}
	});
};
