/**
 * The dry-run form action, as a function two routes share.
 *
 * `FlowDryRun` (@graphene/ui/book/journeys) posts one form, and it should behave identically
 * wherever it is mounted: on its own page at `/flows/[uid]/dry-run`, and — the important one —
 * inside the builder's drawer, where the point is testing the UNSAVED graph on screen. Both need
 * the same body-building, the same gate and the same error shape, so it lives here rather than
 * being written twice and drifting on the second edit.
 *
 * The API endpoint performs nothing (`POST /flows/{uid}/simulate`, three independent guarantees
 * documented on `apps/api/app/routers/flow_insights.py`), so this action is a relay: it never
 * saves, never publishes, and its result is display data.
 */
import { fail, type ActionFailure } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { FlowSimulation } from '@graphene/ui/book/journeys';
import { apiUrl, jsonHeaders } from '$lib/api';

/** A 422 from this endpoint carries `detail.issues` (the definition could not be read); anything
 *  else carries a plain `detail`. Both end up as one sentence for the panel's error banner. */
async function message(res: Response): Promise<string> {
	try {
		const body = await res.json();
		const detail = body?.detail;
		if (typeof detail === 'string') return detail;
		if (Array.isArray(detail?.issues) && detail.issues.length) return detail.issues.join(' ');
		if (typeof detail?.message === 'string') return detail.message;
		if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
	} catch {
		/* fall through */
	}
	return `The preview failed (${res.status}). Please try again.`;
}

/** Split "ISA, SIPP" into product codes. Empty entries dropped so a trailing comma is not a
 *  phantom account in the simulated application. */
function products(raw: string): string[] {
	return raw
		.split(',')
		.map((part) => part.trim().toUpperCase())
		.filter(Boolean);
}

export type DryRunOutcome = { dryRun: FlowSimulation } | ActionFailure<{ dryRunError: string }>;

/**
 * Run the dry run for `flowUid` against the subject described in `form`.
 *
 * `form.definition`, when present, is the draft the author is looking at — the builder's drawer
 * sends the canvas's current JSON so the test is of what is on screen rather than of the last
 * save. Absent ⇒ the API simulates the flow's newest stored version.
 */
export async function runDryRun(
	fetchFn: typeof globalThis.fetch,
	locals: App.Locals,
	flowUid: string,
	form: FormData
): Promise<DryRunOutcome> {
	if (!can(locals.permissions, locals.role, 'flows:write')) {
		return fail(403, {
			dryRunError: 'You cannot preview this journey. Ask a firm administrator for access.'
		});
	}

	const kind = String(form.get('kind') ?? 'real') === 'synthetic' ? 'synthetic' : 'real';
	const subject: Record<string, unknown> = { kind };

	if (kind === 'real') {
		const raw = String(form.get('contact_id') ?? '').trim();
		const contactId = Number(raw);
		if (!raw || !Number.isInteger(contactId) || contactId <= 0) {
			return fail(400, { dryRunError: 'Choose a client to preview this journey.' });
		}
		subject.contact_id = contactId;
	} else {
		subject.contact_type = String(form.get('contact_type') ?? 'individual');
		subject.custody = String(form.get('custody') ?? '') || null;
		subject.products = products(String(form.get('products') ?? ''));
	}

	const body: Record<string, unknown> = { subject };
	const definition = form.get('definition');
	if (typeof definition === 'string' && definition.trim()) {
		try {
			body.definition = JSON.parse(definition);
		} catch {
			return fail(400, { dryRunError: 'The journey on screen could not be read.' });
		}
	}

	const res = await fetchFn(`${apiUrl()}/flows/${flowUid}/simulate`, {
		method: 'POST',
		headers: jsonHeaders(locals.accessToken),
		body: JSON.stringify(body)
	});
	if (!res.ok) return fail(res.status, { dryRunError: await message(res) });
	return { dryRun: (await res.json()) as FlowSimulation };
}
