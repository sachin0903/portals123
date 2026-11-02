import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { printableTerms } from './printable';
import type { RequestHandler } from './$types';

/** What `GET /applications/{uid}/terms` answers with. */
interface TermsCopy {
	name: string | null;
	content: string | null;
	document_uid: string | null;
	content_type: string | null;
	client_name: string | null;
}

// A printable copy of the declaration behind a terms step — `?case=&step=&participant=`, opened
// from the Record acceptance dialog. The acceptance is recorded against a signed copy, and the
// client can only sign what somebody handed them.
//
// A `+server.ts`, not a page: a print sheet has no sidebar, no theme and no hydration, so it is
// served as a complete HTML document (`printable.ts`). Terms authored as a PDF have their bytes
// already — the browser prints those itself — so they go to the same-origin document proxy.
export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'cases:read')) {
		error(403, 'You do not have permission to view applications.');
	}
	const caseUid = url.searchParams.get('case')?.trim() ?? '';
	const stepKey = url.searchParams.get('step')?.trim() ?? '';
	const participant = url.searchParams.get('participant')?.trim() ?? '';
	if (!caseUid || !stepKey) error(400, 'No step specified.');
	const query = new URLSearchParams({ case_uid: caseUid, step_key: stepKey });
	if (participant) query.set('participant_contact_id', participant);
	const res = await fetch(`${apiUrl()}/applications/${params.uid}/terms?${query}`, {
		headers: authHeaders(locals.accessToken)
	});
	if (!res.ok) error(res.status, await detail(res));
	const copy = (await res.json()) as TermsCopy;
	if (copy.content) {
		return new Response(
			printableTerms({
				name: copy.name ?? 'Declaration',
				content: copy.content,
				clientName: copy.client_name,
				firmName: locals.appName,
				printedOn: new Date()
			}),
			{
				headers: {
					'content-type': 'text/html; charset=utf-8',
					'cache-control': 'private, no-store'
				}
			}
		);
	}
	if (copy.document_uid) redirect(303, `/documents/${copy.document_uid}/content`);
	error(404, 'This declaration has no text to print: it is a bare acknowledgement.');
};
