import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, fetchJson } from '$lib/api';
import { toTemplate, type ApiTemplate } from '$lib/templates';
import { toField, type ApiFormField } from '$lib/forms';
import type { ClientOption } from '@graphene/ui/book/documents';
import type { Template, FormField } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

/**
 * Resolve everything the generate dialog needs, server-side and RLS-scoped:
 *   - the template being generated (from ?template=<uid>),
 *   - its linked input form's fields (the real questionnaire — the §9.1 link, resolved by code),
 *   - the tenant's clients (the real "who is this for" picker).
 * The actual render (POST /documents generate) lands with the pipeline — the dialog collects and
 * previews these inputs until then.
 */
export const load: PageServerLoad = async ({ url, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const empty = {
		template: null as Template | null,
		fields: [] as FormField[],
		clients: [] as ClientOption[],
		templateVars: [] as string[],
		loadError: false
	};
	// Generating produces a document — a documents:write action; templates:read to read the template.
	if (!can(locals.permissions, locals.role, 'documents:write')) return { wizard: empty };
	if (!can(locals.permissions, locals.role, 'templates:read')) return { wizard: empty };

	const templateRef = url.searchParams.get('template') ?? '';
	if (!templateRef) return { wizard: empty };
	// Permissions and the empty "no template" case are resolved here; the questionnaire is
	// STREAMED so the generate dialog's shape is on screen on the click.
	return { wizard: loadWizard(templateRef, locals.accessToken, fetch) };
};

async function loadWizard(
	templateRef: string,
	accessToken: string | null,
	fetch: typeof globalThis.fetch
) {
	const empty = {
		template: null as Template | null,
		fields: [] as FormField[],
		clients: [] as ClientOption[],
		templateVars: [] as string[],
		loadError: false
	};
	const headers = authHeaders(accessToken);

	// A failed fetch surfaces as a retry banner rather than a blank dialog (distinct from the
	// legitimately-empty "no template selected" case above).
	const tplRes = await fetchJson<ApiTemplate>(
		fetch,
		`${apiUrl()}/templates/${templateRef}`,
		headers
	);
	if (!tplRes.ok) return { ...empty, loadError: true };
	const tpl: ApiTemplate = tplRes.data;
	const [inputFormRes, clientsRes, varsRes] = await Promise.all([
		fetchJson<{ fields: ApiFormField[]; source: string }>(
			fetch,
			`${apiUrl()}/templates/${templateRef}/input-form`,
			headers
		),
		fetchJson<ClientOption[]>(fetch, `${apiUrl()}/documents/clients`, headers),
		fetchJson<{ variables: string[] }>(fetch, `${apiUrl()}/templates/${tpl.uid}/variables`, headers)
	]);
	const loadError = !inputFormRes.ok || !clientsRes.ok || !varsRes.ok;
	const fields: FormField[] = inputFormRes.ok ? inputFormRes.data.fields.map(toField) : [];
	const clients: ClientOption[] = clientsRes.ok ? clientsRes.data : [];
	const templateVars: string[] = varsRes.ok ? varsRes.data.variables : [];

	return { template: toTemplate(tpl), fields, clients, templateVars, loadError };
}

export const actions: Actions = {
	/** Generate the document from the chosen template + collected inputs (synchronous render). */
	generate: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to generate documents.' });
		}
		let payload: {
			templateUid?: string;
			name?: string;
			clientContactId?: number | null;
			answers?: Record<string, unknown>;
		};
		try {
			payload = JSON.parse(String((await request.formData()).get('payload') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed request.' });
		}
		if (!payload.templateUid) return fail(400, { error: 'No template selected.' });
		const res = await fetch(`${apiUrl()}/documents/generate`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				template_uid: payload.templateUid,
				answers: payload.answers ?? {},
				client_contact_id: payload.clientContactId ?? null,
				name: payload.name ?? null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const doc = await res.json();
		return { ok: true, uid: doc.uid as string };
	},

	/**
	 * Queue the same template for several clients — one document each.
	 *
	 * It does NOT wait for the renders. A single generate polls until the document is finished
	 * because it can then hand the user that document; there is no equivalent for 24 of them, and
	 * polling 24 statuses would turn one click into a minute of spinner. So this returns the API's
	 * own report (how many queued, and which clients were skipped and why) and the page shows it.
	 */
	generateBatch: async ({ request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'documents:write')) {
			return fail(403, { error: 'You do not have permission to generate documents.' });
		}
		let payload: {
			templateUid?: string;
			name?: string;
			contactIds?: number[];
			answers?: Record<string, unknown>;
			idempotencyKey?: string;
		};
		try {
			payload = JSON.parse(String((await request.formData()).get('payload') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed request.' });
		}
		if (!payload.templateUid) return fail(400, { error: 'No template selected.' });
		const contactIds = (payload.contactIds ?? []).filter((n) => Number.isFinite(n));
		if (contactIds.length === 0) {
			return fail(400, { error: 'Choose at least one client to generate for.' });
		}
		const res = await fetch(`${apiUrl()}/documents/generate/batch`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({
				template_uid: payload.templateUid,
				contact_ids: contactIds,
				answers: payload.answers ?? {},
				name: payload.name ?? null,
				// The browser mints it, so a double-submit or a retried request returns the documents
				// the first attempt queued instead of a second render for every client.
				idempotency_key: payload.idempotencyKey ?? null
			})
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const body = (await res.json()) as {
			documents: { uid: string }[];
			rejected: { contact_id: number; reason: string }[];
		};
		return {
			ok: true,
			queued: body.documents.length,
			// Camel-cased at the boundary, as the rest of this app's loaders do.
			rejected: body.rejected.map((r) => ({ contactId: r.contact_id, reason: r.reason }))
		};
	}
};
