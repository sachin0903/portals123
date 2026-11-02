import { error, fail, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { Actions, PageServerLoad } from './$types';

/** One field of a vendor action, as the vendor documents it. */
export type ActionField = {
	name: string;
	type: string;
	required: boolean;
	conditional: boolean;
	required_note: string;
	description: string;
	options: { label: string | null; value: number }[];
	/** Several members at once, sent as one comma-separated string of their numbers. */
	multiselect: boolean;
	/**
	 * Non-empty when this field is a REFERENCE to one of the firm's own records — the entity's
	 * human name ("Contacts"). The drawer then offers a searchable picker instead of a box to
	 * paste a GUID into.
	 */
	lookup: string;
};

export type PlatformAction = {
	key: string;
	label: string;
	action: string;
	group: string;
	method: string;
	prefill: string;
	note: string;
	danger: string;
	/**
	 * Whether this operation is safe to send twice: `safe` | `guarded` | `manual`.
	 *
	 * A fact about the VENDOR, not about our transport. `manual` means there is no way to ask
	 * whether an ambiguous attempt landed — Morgan Lloyd has no delete, so resending a create puts
	 * a second human in the CRM and resending a contribution reclaims relief from HMRC twice. The
	 * drawer says so before the operator fills the form in.
	 */
	retry: 'safe' | 'guarded' | 'manual';
	documented: boolean;
	fields: ActionField[];
	lookups: Record<string, string>[];
	/**
	 * Values fixed for this integration, keyed by vendor field name.
	 *
	 * The vendor issues them per integrating firm in a Word document and none is discoverable
	 * from the API, so the alternative is an operator retyping `809130016` — and the vendor
	 * validates the Create Scheme pair against the caller's role rather than saying which half
	 * is wrong. Absent when the deployment has not configured one; the field then renders empty
	 * and the 422 names it.
	 */
	defaults: Record<string, string>;
	/** URL parameters — the record id a PATCH targets, in the vendor's own words. */
	parameters: ActionField[];
};

export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { provider } = await parent();

	// `?firm=` resolves the prefilled defaults from THIS firm's connection rather than from
	// deployment env — one Dynamics org per firm, so the connection is the more specific answer.
	const res = await fetch(
		`${apiUrl()}/admin/platforms/${provider}/actions?firm=${encodeURIComponent(params.uid)}`,
		{ headers: authHeaders(locals.accessToken) }
	);
	if (!res.ok) error(res.status, await detail(res));

	return {
		actions: (await res.json()) as PlatformAction[],
		// Read and write are separate permissions: an observer sees WHAT can be done and the
		// contract for it, and cannot fire one. The drawer renders read-only without this.
		canWrite: can(locals.permissions, locals.role, 'platforms:write')
	};
};

export const actions: Actions = {
	run: async ({ request, locals, params, fetch }) => {
		if (!can(locals.permissions, locals.role, 'platforms:write')) {
			return fail(403, { message: 'You do not have permission to perform platform actions.' });
		}
		const form = await request.formData();
		const key = String(form.get('__action') ?? '');
		const entityId = String(form.get('__entity_id') ?? '').trim();

		// Everything else on the form is a vendor field. Blank values are dropped here rather
		// than sent as "": for an option set the vendor rejects an empty string outright, and
		// "not supplied" is what an empty box means.
		const payload: Record<string, string> = {};
		for (const [name, value] of form.entries()) {
			if (name.startsWith('__')) continue;
			const text = String(value).trim();
			if (text) payload[name] = text;
		}

		// Resolved from the slug map, not from the layout: a form action cannot read layout data
		// (the same constraint that gave `$lib/api::tenantIdFor` its reason to exist).
		const provider = params.slug;
		if (!provider) return fail(404, { message: 'This platform has no live actions.' });
		const res = await fetch(
			`${apiUrl()}/admin/platforms/${provider}/firms/${params.uid}/actions/${key}`,
			{
				method: 'POST',
				headers: { ...authHeaders(locals.accessToken), 'content-type': 'application/json' },
				body: JSON.stringify({ payload, entity_id: entityId || null })
			}
		);

		const body = await res.json().catch(() => ({}));
		if (!res.ok) {
			// The API's 422 names the field; surfaced verbatim because rewording it loses the one
			// thing that makes it actionable.
			return fail(res.status, {
				key,
				message: (body as { detail?: string }).detail ?? `${res.status} ${res.statusText}`
			});
		}
		return { key, success: true, response: body };
	}
};
