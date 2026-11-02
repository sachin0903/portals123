import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail } from '$lib/api';
import {
	toTemplate,
	type ApiTemplate,
	type ApiVersion,
	type ApiVersionDetail
} from '$lib/templates';
import type { ApiForm, ApiFormField } from '$lib/forms';
import type { Template } from '@graphene/ui';
import type { Actions, PageServerLoad } from './$types';

/** A template version as the editor's history panel shows it (real server versions, not a local
 *  session log). The blob is fetched on demand via /templates/[uid]/versions/[versionUid]. */
export type EditorVersion = {
	uid: string;
	versionNumber: number;
	status: string;
	savedAt: string;
	author: string | null;
};

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const empty = {
		template: null as Template | null,
		restore: null as Record<string, unknown> | null,
		formFields: [] as { key: string; label: string }[],
		versions: [] as EditorVersion[],
		datasetOptions: [] as {
			code: string;
			name: string;
			columns: string[];
			uid: string;
			scope: string;
		}[],
		previewClients: [] as { contactId: number; name: string }[],
		brandLogo: null as string | null,
		canManage: false
	};
	if (!can(locals.permissions, locals.role, 'templates:read')) return empty;
	const headers = authHeaders(locals.accessToken);
	const tplRes = await fetch(`${apiUrl()}/templates/${params.uid}`, { headers });
	if (!tplRes.ok) return empty;
	const tpl: ApiTemplate = await tplRes.json();
	const [versRes, formsRes, dsRes, clRes] = await Promise.all([
		fetch(`${apiUrl()}/templates/${params.uid}/versions`, { headers }),
		tpl.input_form_code ? fetch(`${apiUrl()}/forms`, { headers }) : Promise.resolve(null),
		fetch(`${apiUrl()}/datasets?include=sample`, { headers }),
		fetch(`${apiUrl()}/documents/clients`, { headers })
	]);
	const versions: ApiVersion[] = versRes.ok ? await versRes.json() : [];
	let restore: Record<string, unknown> | null = null;
	if (versions[0]) {
		const vRes = await fetch(`${apiUrl()}/templates/${params.uid}/versions/${versions[0].uid}`, {
			headers
		});
		if (vRes.ok) {
			const detailV: ApiVersionDetail = await vRes.json();
			restore = detailV.schema && Object.keys(detailV.schema).length ? detailV.schema : null;
		}
	}
	let formFields: { key: string; label: string }[] = [];
	if (tpl.input_form_code && formsRes?.ok) {
		const forms: ApiForm[] = await formsRes.json();
		const linked = forms.find((f) => f.code === tpl.input_form_code);
		if (linked) {
			const fRes = await fetch(`${apiUrl()}/forms/${linked.uid}/fields`, { headers });
			const fields: ApiFormField[] = fRes.ok ? await fRes.json() : [];
			formFields = fields.map((f) => ({ key: f.field_key, label: f.label }));
		}
	}
	// The real version history for the editor's History panel (newest first, as the API returns).
	const editorVersions: EditorVersion[] = versions.map((v) => ({
		uid: v.uid,
		versionNumber: v.version_number,
		status: v.status,
		savedAt: v.published_at ?? v.created_at,
		author: v.published_by ?? v.created_by
	}));
	// Real datasets for the chart/table config pickers — names plus (for static datasets) their
	// column schema from the published sample, so the field pickers resolve real fields instead of
	// the old free-text guessing. Tolerant: no datasets:read just means an empty picker.
	let datasetOptions: {
		code: string;
		name: string;
		columns: string[];
		uid: string;
		scope: string;
		/** The dataset's stored sample (inline data, or an authored sample_data preview) — used to
		    fill a bound table on the editor canvas with real rows instead of literal {{ tokens }}. */
		sample: { columns: string[]; rows: unknown[][] } | null;
	}[] = [];
	if (dsRes.ok) {
		const list: {
			uid: string;
			code: string;
			name: string;
			scope: string;
			columns?: string[];
			sample: { columns: string[]; rows: unknown[][] } | null;
		}[] = await dsRes.json();
		datasetOptions = list.map((d) => ({
			code: d.code,
			name: d.name,
			uid: d.uid,
			scope: d.scope,
			columns: d.columns ?? d.sample?.columns ?? [],
			sample: d.sample ?? null
		}));
	}
	// Clients for the chart-preview's subject picker (subject-scoped datasets resolve per client).
	// Tolerant: no documents:write just means the preview falls back to a sample resolve.
	let previewClients: { contactId: number; name: string }[] = [];
	if (clRes.ok) {
		const clients: { contact_id: number; name: string | null }[] = await clRes.json();
		previewClients = clients.map((c) => ({
			contactId: c.contact_id,
			name: c.name ?? `Client ${c.contact_id}`
		}));
	}
	return {
		template: toTemplate(tpl),
		restore,
		formFields,
		versions: editorVersions,
		datasetOptions,
		previewClients,
		// The tenant's public logo URL (client-facing brand), baked into the Letterhead smart component.
		brandLogo: locals.branding?.logo_url ?? null,
		canManage: can(locals.permissions, locals.role, 'templates:write')
	};
};

export const actions: Actions = {
	/** Save the editor's blob as the template's current draft (upserts the latest draft version). */
	saveVersion: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'templates:write')) {
			return fail(403, { error: 'You do not have permission to edit templates.' });
		}
		let schema: unknown;
		try {
			schema = JSON.parse(String((await request.formData()).get('schema') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed template content.' });
		}
		const res = await fetch(`${apiUrl()}/templates/${params.uid}/versions`, {
			method: 'POST',
			headers: jsonHeaders(locals.accessToken),
			body: JSON.stringify({ schema })
		});
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		return { ok: true };
	}
};
