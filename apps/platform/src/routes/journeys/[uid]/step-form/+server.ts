/**
 * A staff `fill_form` step, filled WITHOUT leaving the journey.
 *
 * The same form and the same submit as `steps/[case_uid]/[step_key]`, which stays as the
 * addressable page (a link in a task, a bookmark, a browser back button). What this adds is the
 * ability to do it in place: four consecutive fact-find forms were four navigations away and back,
 * and finding your place in a fifteen-row list each time is most of what made the page feel like
 * admin rather than work.
 *
 * Deliberately the SAME API endpoints, not a shortcut past them — the submit routes through the
 * fulfilment hook the client path uses, so the CRM write-back mapping runs identically whoever
 * filled it in. A second door with its own rules is how the two paths would drift.
 */
import { json, error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import type { RequestHandler } from './$types';

/** The step's form lives under its case + key, and a fanned step needs the participant too. */
function formUrl(uid: string, caseUid: string, key: string, participant: string | null): string {
	const base = `${apiUrl()}/applications/${encodeURIComponent(uid)}/cases/${encodeURIComponent(caseUid)}/steps/${encodeURIComponent(key)}/form`;
	return participant ? `${base}?participant_contact_id=${encodeURIComponent(participant)}` : base;
}

function ask(url: URL): { caseUid: string; key: string; participant: string | null } {
	const caseUid = url.searchParams.get('case') ?? '';
	const key = url.searchParams.get('step') ?? '';
	if (!caseUid || !key) error(400, 'No step specified.');
	return { caseUid, key, participant: url.searchParams.get('participant') };
}

export const GET: RequestHandler = async ({ params, url, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in.');
	if (!can(locals.permissions, locals.role, 'cases:read')) {
		error(403, 'You do not have permission to view journeys.');
	}
	const { caseUid, key, participant } = ask(url);
	const res = await fetch(formUrl(params.uid, caseUid, key, participant), {
		headers: authHeaders(locals.accessToken)
	});
	// 422 is "this step has no form", which is a real answer about the step rather than a fault —
	// the sheet says so instead of showing a red box over an empty panel.
	if (res.status === 422) return json({ form: null, reason: 'This step has no form to complete.' });
	if (!res.ok) error(res.status === 404 ? 404 : 502, 'Could not load this step’s form.');
	return json({ form: await res.json() });
};

export const POST: RequestHandler = async ({ params, url, request, locals, fetch }) => {
	if (!locals.user) error(401, 'Not signed in.');
	if (!can(locals.permissions, locals.role, 'cases:write')) {
		error(403, 'You do not have permission to complete journey steps.');
	}
	const { caseUid, key, participant } = ask(url);
	const answers = await request.json();
	const res = await fetch(formUrl(params.uid, caseUid, key, participant), {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify({
			answers,
			participant_contact_id: participant ? Number(participant) : null
		})
	});
	if (!res.ok) error(res.status, await detail(res));
	return json({ ok: true });
};
