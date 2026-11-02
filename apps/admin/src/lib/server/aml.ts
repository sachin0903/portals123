import { error, fail, json, redirect, type RequestEvent } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail, jsonHeaders, type TenantSummary } from '$lib/api';
import { PHOTO_REQUEST_REASONS, clientHref, type AmlClientRow } from '$lib/aml';
import { can } from '$lib/permissions';

/**
 * One firm client's row in Graphene's own AML console: the latest check of each kind, the risk
 * level, and the key of their page there.
 *
 * The console is not a firm-scoped API, so this is not a target-tenant call. The firm is still always
 * named: `tenant` resolves the console's auth context to that firm, so a client uid from another firm
 * finds nothing. `client` is null when the console has nothing for them; the whole answer is null
 * when the call fails.
 */
export async function clientChecks(
	fetch: typeof globalThis.fetch,
	token: string | null,
	tenantUid: string,
	contactUid: string
): Promise<{ client: AmlClientRow | null } | null> {
	const query = new URLSearchParams({ tenant: tenantUid, contact: contactUid });
	try {
		const res = await fetch(`${apiUrl()}/aml/clients?${query}`, { headers: authHeaders(token) });
		if (!res.ok) return null;
		const items = ((await res.json()).items ?? []) as AmlClientRow[];
		return { client: items[0] ?? null };
	} catch {
		return null;
	}
}

/** A JSON write to the API; null when the service cannot be reached. */
export async function send(
	fetch: typeof globalThis.fetch,
	token: string | null,
	path: string,
	method: 'POST' | 'PUT',
	body: unknown
): Promise<Response | null> {
	try {
		return await fetch(`${apiUrl()}${path}`, {
			method,
			headers: jsonHeaders(token),
			body: JSON.stringify(body)
		});
	} catch {
		return null;
	}
}

/** The check a form acts on. Every act names its check: a page can show several. */
export function checkPath(form: FormData): string | null {
	const uid = String(form.get('check') ?? '').trim();
	return uid ? `/aml/checks/${encodeURIComponent(uid)}` : null;
}

const MATCH_DECISIONS = ['confirmed', 'dismissed'] as const;

/**
 * The `decideMatch` form action, shared by the client page and the workbench: confirm or reject
 * one match, as ComplyCube does, with an optional comment.
 */
export async function decideMatch({ request, locals, fetch }: RequestEvent) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:adjudicate')) error(403, 'Not authorised');

	const form = await request.formData();
	const check = checkPath(form);
	const match = String(form.get('match') ?? '').trim();
	const decision = String(form.get('decision') ?? '');
	const comment = String(form.get('comment') ?? '').trim();
	if (!check || !match) return fail(400, { action: 'decideMatch', error: 'No match given.' });
	if (!(MATCH_DECISIONS as readonly string[]).includes(decision))
		return fail(400, {
			action: 'decideMatch',
			match,
			error: 'Choose True match or False positive.'
		});

	const res = await send(
		fetch,
		locals.accessToken,
		`${check}/matches/${encodeURIComponent(match)}/decision`,
		'POST',
		{ decision, comment: comment || null }
	);
	if (!res)
		return fail(503, { action: 'decideMatch', match, error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'decideMatch', match, error: await detail(res) });
	return { action: 'decideMatch', match, decision };
}

// The form actions the check sections (`identity-section`, `aml-section`) post. Any page that
// renders those sections lists these in its own `actions`.

const CHECK_DECISIONS = ['clear', 'rejected'] as const;
const PHOTO_CHOICES = ['document', 'selfie'] as const;
const PHOTO_REQUEST_REASON_CODES = PHOTO_REQUEST_REASONS.map((r) => r.value);

/**
 * The reason and note as one comment. The structured reason leads so the record reads the same in
 * ComplyCube and here; the note says what was checked.
 */
function comment(reason: string, note: string): string {
	return note ? `${reason}: ${note}` : reason;
}

/**
 * Let a portal client's journey continue past a confirmed AML match. Until then the journey
 * waits, and the firm sees the screen as in progress. The comment is optional.
 */
export async function allowJourney({ request, locals, fetch }: RequestEvent) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:adjudicate')) error(403, 'Not authorised');

	const form = await request.formData();
	const check = checkPath(form);
	const note = String(form.get('comment') ?? '').trim();
	if (!check) return fail(400, { action: 'allowJourney', error: 'No check given.' });

	const res = await send(fetch, locals.accessToken, `${check}/allow`, 'POST', {
		comment: note || null
	});
	if (!res) return fail(503, { action: 'allowJourney', error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'allowJourney', error: await detail(res) });
	return { action: 'allowJourney' };
}

/** Clear or reject an identity check that referred. Both need a reason. */
export async function decideCheck({ request, locals, fetch }: RequestEvent) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:adjudicate')) error(403, 'Not authorised');

	const form = await request.formData();
	const check = checkPath(form);
	const decision = String(form.get('decision') ?? '');
	const reason = String(form.get('reason') ?? '').trim();
	const note = String(form.get('note') ?? '').trim();
	if (!check) return fail(400, { action: 'decideCheck', error: 'No check given.' });
	if (!(CHECK_DECISIONS as readonly string[]).includes(decision))
		return fail(400, { action: 'decideCheck', error: 'Choose a decision.' });
	if (!reason) return fail(400, { action: 'decideCheck', error: 'Choose a reason.' });
	if (reason === 'Other' && !note)
		return fail(400, { action: 'decideCheck', error: 'Describe the reason in the note.' });

	const res = await send(fetch, locals.accessToken, `${check}/decision`, 'POST', {
		decision,
		comment: comment(reason, note)
	});
	if (!res) return fail(503, { action: 'decideCheck', error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'decideCheck', error: await detail(res) });
	return { action: 'decideCheck', decision };
}

/**
 * Ask the client for new photos instead of deciding the check: it stays referred but a new try
 * opens for them. The note is internal — never shown to the client, who sees only the reason's
 * text.
 */
export async function requestPhotos({ request, locals, fetch }: RequestEvent) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:adjudicate')) error(403, 'Not authorised');

	const form = await request.formData();
	const check = checkPath(form);
	const photos = [...new Set(form.getAll('photos').map((p) => String(p)))];
	const reason = String(form.get('reason') ?? '').trim();
	const note = String(form.get('note') ?? '').trim();

	if (!check) return fail(400, { action: 'requestPhotos', error: 'No check given.' });
	if (!photos.length || photos.some((p) => !(PHOTO_CHOICES as readonly string[]).includes(p)))
		return fail(400, { action: 'requestPhotos', error: 'Choose at least one photo.' });
	if (!(PHOTO_REQUEST_REASON_CODES as readonly string[]).includes(reason))
		return fail(400, { action: 'requestPhotos', error: 'Choose what the client will be told.' });
	if (!note) return fail(400, { action: 'requestPhotos', error: 'Add a note for the record.' });

	const res = await send(fetch, locals.accessToken, `${check}/request-photos`, 'POST', {
		photos,
		reason,
		comment: note
	});
	if (!res) return fail(503, { action: 'requestPhotos', error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'requestPhotos', error: await detail(res) });
	return { action: 'requestPhotos' };
}

/** Turn ongoing monitoring on or off for an AML check. */
export async function monitoring({ request, locals, fetch }: RequestEvent) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:write')) error(403, 'Not authorised');

	const form = await request.formData();
	const check = checkPath(form);
	if (!check) return fail(400, { action: 'monitoring', error: 'No check given.' });
	const enabled = form.get('enabled') === 'on';
	const res = await send(fetch, locals.accessToken, `${check}/monitoring`, 'PUT', { enabled });
	if (!res) return fail(503, { action: 'monitoring', error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'monitoring', error: await detail(res) });
	return { action: 'monitoring', enabled };
}

/** Run a failed screen again, for a portal or a partner client. The new screen becomes the
 *  client's AML screen; the failed one moves to their earlier checks. */
export async function rerun({ request, locals, fetch }: RequestEvent) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:write')) error(403, 'Not authorised');

	const form = await request.formData();
	const check = checkPath(form);
	if (!check) return fail(400, { action: 'rerun', error: 'No check given.' });
	const res = await send(fetch, locals.accessToken, `${check}/rerun`, 'POST', {});
	if (!res) return fail(503, { action: 'rerun', error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'rerun', error: await detail(res) });
	return { action: 'rerun' };
}

/**
 * Screen a portal client who is due one, or again after their details changed. The API answers
 * with the open check when one already exists, so a double click costs one paid screen. A client
 * screened for the first time gets their lasting key, so the page moves to it.
 */
export async function screen(event: RequestEvent) {
	return screenClient(event, true);
}

/** The same screen for a page that stays where it is: the workbench moves through its own
 *  queue, so it never moves to the client page. */
export async function screenInPlace(event: RequestEvent) {
	return screenClient(event, false);
}

async function screenClient({ request, locals, fetch }: RequestEvent, follow: boolean) {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'aml:write')) error(403, 'Not authorised');

	const form = await request.formData();
	const contact = String(form.get('contact_uid') ?? '').trim();
	if (!contact) return fail(400, { action: 'screen', error: 'No client given.' });
	const res = await send(fetch, locals.accessToken, '/aml/screen', 'POST', {
		contact_uid: contact
	});
	if (!res) return fail(503, { action: 'screen', error: 'The service is unavailable.' });
	if (!res.ok) return fail(res.status, { action: 'screen', error: await detail(res) });
	if (!follow) return { action: 'screen' };
	const body = await res.json();
	const started = (body?.check_uid ?? null) as string | null;
	let key: string | null = null;
	if (started) {
		try {
			const found = await fetch(`${apiUrl()}/aml/checks/${encodeURIComponent(started)}/client`, {
				headers: authHeaders(locals.accessToken)
			});
			if (found.ok) key = ((await found.json())?.key ?? null) as string | null;
		} catch {
			/* the reload below still lands: a contact key resolves to the screened client */
		}
	}
	if (key) redirect(303, clientHref(key));
	return { action: 'screen' };
}

/**
 * The firm picker for the console's lists. `/admin/tenants` needs `tenants:read`, which an
 * AML-only role need not hold, so a refusal falls back to the firms present in the current result:
 * narrower, but it never leaves the picker empty for someone who can see the list.
 */
export async function firmChoices(
	fetch: typeof globalThis.fetch,
	token: string | null,
	present: { uid: string; name: string }[]
): Promise<{ uid: string; name: string }[]> {
	let firms: { uid: string; name: string }[] = [];
	try {
		const res = await fetch(`${apiUrl()}/admin/tenants`, { headers: authHeaders(token) });
		if (res.ok) {
			const tenants: TenantSummary[] = await res.json();
			firms = tenants.map((t) => ({ uid: t.uid, name: t.name }));
		}
	} catch {
		/* fall through to the derived list */
	}
	if (firms.length === 0)
		firms = [...new Map(present.map((f) => [f.uid, f.name]))].map(([uid, name]) => ({ uid, name }));
	return firms.sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * A JSON call the page makes through this app: an auth and `aml:read` gate, then the API's body
 * and status as they are, never cached. With `send` it posts that JSON (`undefined` when the page
 * sent none it could read: a 400). A refusal here answers in the API's own shape (`{detail}`), so
 * the page reads one shape; no answer at all is a 503.
 */
export async function relayJson(
	{ locals, fetch }: Pick<RequestEvent, 'locals' | 'fetch'>,
	path: string,
	send?: { json: unknown }
): Promise<Response> {
	const headers = { 'cache-control': 'private, no-store' };
	if (!locals.user) return json({ detail: 'Not signed in.' }, { status: 401, headers });
	if (!can(locals.permissions, locals.role, 'aml:read'))
		return json({ detail: 'Not authorised' }, { status: 403, headers });
	if (send && send.json === undefined)
		return json({ detail: 'Send a JSON body.' }, { status: 400, headers });
	let res: Response;
	try {
		res = await fetch(
			`${apiUrl()}${path}`,
			send
				? {
						method: 'POST',
						headers: jsonHeaders(locals.accessToken),
						body: JSON.stringify(send.json)
					}
				: { headers: authHeaders(locals.accessToken) }
		);
	} catch {
		return json({ detail: 'The service is unavailable.' }, { status: 503, headers });
	}
	// An empty answer (a 204) passes on as no body at all: a 204 may not carry one.
	const body = await res.text();
	return new Response(body || null, {
		status: res.status,
		headers: { 'content-type': res.headers.get('content-type') ?? 'application/json', ...headers }
	});
}

/** How many clients are in the work queue, for the console's tab; null when the call fails. */
export async function openWorkCount(
	fetch: typeof globalThis.fetch,
	token: string | null,
	tenant: string
): Promise<number | null> {
	const query = tenant ? `?${new URLSearchParams({ tenant })}` : '';
	try {
		const res = await fetch(`${apiUrl()}/aml/work/summary${query}`, {
			headers: authHeaders(token)
		});
		return res.ok ? Number((await res.json()).total ?? 0) : null;
	} catch {
		return null;
	}
}

/**
 * One of Graphene's compliance reports as a PDF, relayed from the API `path`
 * (`/aml/clients/{key}/report`, `/aml/checks/{uid}/report`) so the browser never talks to the API. The
 * bytes are buffered, like the photo relay's; the type and file name pass through, and the report is
 * never cached. A 403, 404, 409 or 503 (the PDF could not be made) passes through with the API's
 * reason; any other refusal is a 502, and no answer at all a 503.
 */
/** The API's reason a report could not be made, else a plain one. */
async function unmade(res: Response): Promise<string> {
	const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
	return typeof body?.detail === 'string'
		? body.detail
		: 'The report could not be made. Try again.';
}

export async function relayReport(
	fetch: typeof globalThis.fetch,
	token: string | null,
	path: string
): Promise<Response> {
	let res: Response;
	try {
		res = await fetch(`${apiUrl()}${path}`, { headers: authHeaders(token) });
	} catch {
		error(503, 'The report is unavailable.');
	}
	if (!res.ok) {
		if (res.status === 403 || res.status === 404 || res.status === 409)
			error(res.status, await detail(res));
		if (res.status === 503) error(503, await unmade(res));
		error(502, 'The report is unavailable.');
	}
	const body = await res.arrayBuffer();
	return new Response(body, {
		headers: {
			'content-type': res.headers.get('content-type') ?? 'application/pdf',
			'content-disposition': res.headers.get('content-disposition') ?? 'attachment',
			'cache-control': 'private, no-store'
		}
	});
}
