import { error, fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import { parseInlineTable } from '$lib/csv';
import {
	toDataset,
	toDatasetVersion,
	type ApiDatasetDetail,
	type ApiCatalogueEntry,
	type ApiDatasetVersion,
	type DatasetSampleData
} from '$lib/datasets';
import type { Actions, PageServerLoad } from './$types';

/** A client the author can resolve a subject-scoped dataset against (GET /datasets/subjects). */
export interface DatasetSubject {
	contact_id: number;
	name: string;
}

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'datasets:read')) error(403, 'Not permitted');
	const headers = authHeaders(locals.accessToken);
	const [res, versRes, subjRes, catRes] = await Promise.all([
		fetch(`${apiUrl()}/datasets/${params.uid}`, { headers }),
		fetch(`${apiUrl()}/datasets/${params.uid}/versions`, { headers }),
		// For the "Run query" panel's client picker; scoped to the caller's own book by RLS.
		fetch(`${apiUrl()}/datasets/subjects`, { headers }),
		// The provided entries, so a configured dataset's settings form knows what it may set — the
		// declaration lives in the API, not in this page.
		fetch(`${apiUrl()}/datasets/catalogue`, { headers })
	]);
	if (!res.ok) error(404, 'Dataset not found');
	const detail: ApiDatasetDetail & { used_by_templates?: number } = await res.json();
	const versions: ApiDatasetVersion[] = versRes.ok ? await versRes.json() : [];
	const subjects: DatasetSubject[] = subjRes.ok ? await subjRes.json() : [];
	const catalogue: ApiCatalogueEntry[] = catRes.ok ? await catRes.json() : [];
	return {
		catalogue,
		dataset: toDataset(detail),
		sample: detail.sample as DatasetSampleData | null,
		columns: detail.columns ?? [],
		versions: versions.map(toDatasetVersion),
		// Live templates binding this dataset's code — deletion is refused while any exist, and the
		// confirm dialog says so instead of promising a clean removal.
		usedByTemplates: detail.used_by_templates ?? 0,
		subjects,
		subjectsError: !subjRes.ok,
		canManage: can(locals.permissions, locals.role, 'datasets:write'),
		// A firm-wide dataset is resolved across the WHOLE firm when a document is generated, so the
		// API requires 'contacts:read_all' to set one up (403 otherwise). Passed
		// through so the scope control isn't offered as working to someone it will refuse.
		canFirmWide: can(locals.permissions, locals.role, 'contacts:read_all')
	};
};

async function apiJson(
	locals: App.Locals,
	fetch: typeof globalThis.fetch,
	path: string,
	method: string,
	body: Record<string, unknown>
) {
	const res = await fetch(`${apiUrl()}${path}`, {
		method,
		headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
	if (!res.ok) {
		const detail = await res.json().catch(() => ({}));
		return fail(res.status, {
			error: (detail as { detail?: string }).detail ?? 'That didn’t work.'
		});
	}
	return { ok: true };
}

export const actions: Actions = {
	// Edit metadata (name / description / scope / enabled). Format is immutable, and liveness follows
	// the format, so neither is sent.
	updateDetails: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'datasets:write')) error(403, 'Not permitted');
		const form = await request.formData();
		const name = String(form.get('name') ?? '').trim();
		if (!name) return fail(400, { error: 'Name is required.' });
		// The scope control only exists for a SQL dataset (nothing else can be per-client), so when
		// it's absent leave scope alone rather than defaulting it — renaming an inline dataset must
		// not quietly rewrite a field the form never offered.
		const scope = form.get('scope');
		return apiJson(locals, fetch, `/datasets/${params.uid}`, 'PATCH', {
			name,
			description: String(form.get('description') ?? '').trim() || null,
			...(scope ? { scope: String(scope) } : {}),
			enabled: form.get('enabled') === 'on'
		});
	},

	// Publish a new version carrying edited data — the inline grid (columns/rows), or the chosen
	// settings for a provided dataset. The dataset's format is fixed, so only the matching source
	// is sent.
	addVersion: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'datasets:write')) error(403, 'Not permitted');
		const form = await request.formData();
		const format = String(form.get('format') ?? '');
		if (format === 'catalogue') {
			const key = String(form.get('catalogue_key') ?? '').trim();
			if (!key) return fail(400, { error: 'This dataset has no provided dataset behind it.' });
			// Every `param_*` field, VERBATIM — a relative range (`-12m`) has to reach the database
			// unchanged, or the dataset freezes on whatever today's date happened to be.
			const chosen: Record<string, string> = {};
			for (const [field, raw] of form.entries()) {
				if (field.startsWith('param_')) chosen[field.slice('param_'.length)] = String(raw);
			}
			return apiJson(locals, fetch, `/datasets/${params.uid}/versions`, 'POST', {
				catalogue: { key, params: chosen }
			});
		}
		if (format === 'inline') {
			const { columns, rows, raggedRows } = parseInlineTable(
				String(form.get('columns') ?? ''),
				String(form.get('rows') ?? '')
			);
			if (!columns.length) return fail(400, { error: 'Add at least one column.' });
			if (raggedRows.length) {
				return fail(400, { error: 'Some rows don’t match the column count.' });
			}
			return apiJson(locals, fetch, `/datasets/${params.uid}/versions`, 'POST', {
				inline: { columns, rows }
			});
		}
		return fail(400, { error: `Editing data isn’t supported for ${format} datasets.` });
	},

	// Resolve the dataset now, against an optional client — the only way to find out what a dataset
	// actually returns for a real one (GET /datasets/{uid}/data). Failures come back with the API's
	// own reason (a bad setting, a missing client, …) rather than an empty grid.
	runQuery: async ({ params, request, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'datasets:read')) error(403, 'Not permitted');
		const form = await request.formData();
		const contactId = String(form.get('contact_id') ?? '').trim();
		const qs = contactId ? `?contact_id=${encodeURIComponent(contactId)}` : '';
		const res = await fetch(`${apiUrl()}/datasets/${params.uid}/data${qs}`, {
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok) {
			const detail = await res.json().catch(() => ({}));
			return fail(res.status, {
				runError: (detail as { detail?: string }).detail ?? 'The query didn’t run.'
			});
		}
		const data = (await res.json()) as {
			columns?: string[];
			rows?: unknown[][];
			truncated?: boolean;
			row_limit?: number;
			notice?: string | null;
		};
		return {
			run: {
				columns: data.columns ?? [],
				rows: data.rows ?? [],
				truncated: data.truncated ?? false,
				rowLimit: data.row_limit ?? 0,
				notice: data.notice ?? null
			}
		};
	},

	delete: async ({ params, locals, fetch }) => {
		if (!can(locals.permissions, locals.role, 'datasets:write')) error(403, 'Not permitted');
		const res = await fetch(`${apiUrl()}/datasets/${params.uid}`, {
			method: 'DELETE',
			headers: authHeaders(locals.accessToken)
		});
		if (!res.ok && res.status !== 204) {
			// 409 = live templates still bind this dataset. Surface the API's message (it carries the
			// count) instead of a generic failure.
			const detail = await res.json().catch(() => ({}));
			return fail(res.status, {
				error: (detail as { detail?: string }).detail ?? 'Could not delete the dataset.'
			});
		}
		// The datasets VIEW of the templates surface — `/datasets` was deleted when datasets folded
		// into it, so the old path is a 404 rather than a redirect.
		redirect(303, '/templates/datasets');
	}
};
