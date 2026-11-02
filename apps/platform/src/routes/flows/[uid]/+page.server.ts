import { fail, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, jsonHeaders, detail, type Client } from '$lib/api';
import { itemsOf, type ListingPage } from '@graphene/ui/book/listing';
import { runDryRun } from '$lib/flow-dry-run';
import type {
	FlowOut,
	FlowVersionOut,
	FlowForm,
	FlowDocument,
	FlowTerm,
	FlowTemplate,
	CatalogProduct,
	DryRunClient
} from '@graphene/ui/book/journeys';

/** The dry-run subject picker's list. Capped for the same reason C3's standalone page caps it: a
 *  firm with ten thousand clients does not want them all in a dropdown, and the described-subject
 *  mode covers anyone outside the first slice. */
const MAX_DRY_RUN_CLIENTS = 200;

function clientLabel(client: Client): string {
	const person = [client.first_name, client.surname].filter(Boolean).join(' ').trim();
	return person || client.name || client.email || `Client ${client.id}`;
}

/** A firm role for the raise-a-task assignee picker (slug is what travels in the definition). */
type FlowRoleOption = { slug: string; name: string };
/** Another journey this one can hand over to (`start_flow`). The definition travels the CODE, not
 *  the uid — a tail call names a flow by its stable slug so a re-published version still resolves. */
type FlowHandoffTarget = { uid: string; code: string; name: string };
type TermListItem = { uid: string; title: string };
import type { Actions, PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'flows:read')) {
		return {
			flow: null as FlowOut | null,
			version: null as FlowVersionOut | null,
			forms: [] as FlowForm[],
			documents: [] as FlowDocument[],
			templates: [] as FlowTemplate[],
			terms: [] as FlowTerm[],
			products: [] as CatalogProduct[],
			roles: [] as FlowRoleOption[],
			flows: [] as FlowHandoffTarget[],
			dryRunClients: [] as DryRunClient[],
			canManage: false
		};
	}
	const headers = authHeaders(locals.accessToken);
	const [
		flowRes,
		versionsRes,
		formsRes,
		documentsRes,
		templatesRes,
		termsRes,
		productsRes,
		rolesRes,
		flowsRes,
		clientsRes
	] = await Promise.all([
		fetch(`${apiUrl()}/flows/${params.uid}`, { headers }),
		fetch(`${apiUrl()}/flows/${params.uid}/versions`, { headers }),
		fetch(`${apiUrl()}/forms`, { headers }),
		fetch(`${apiUrl()}/documents?limit=${MAX_DRY_RUN_CLIENTS}`, { headers }),
		fetch(`${apiUrl()}/templates`, { headers }),
		fetch(`${apiUrl()}/terms?status=published`, { headers }),
		fetch(`${apiUrl()}/catalog/products`, { headers }),
		// For the raise-a-task assignee picker; a flow author without members:read just gets
		// a free-text slug input instead of suggestions.
		fetch(`${apiUrl()}/firm/roles`, { headers }),
		// The other journeys this one can hand over to (`start_flow`).
		fetch(`${apiUrl()}/flows`, { headers }),
		// The dry-run drawer's subject picker. RLS scopes this to the caller's own book, so it can
		// only ever offer clients they may already see; a failure degrades the picker to the
		// described-subject mode rather than failing the page.
		fetch(`${apiUrl()}/crm/contacts?limit=${MAX_DRY_RUN_CLIENTS}`, { headers })
	]);
	if (!flowRes.ok) {
		return {
			flow: null,
			version: null,
			forms: [],
			documents: [],
			templates: [],
			terms: [],
			products: [],
			roles: [] as FlowRoleOption[],
			flows: [] as FlowHandoffTarget[],
			dryRunClients: [] as DryRunClient[],
			canManage: false
		};
	}
	const flow: FlowOut = await flowRes.json();
	// Newest-first; the latest version seeds the builder (its draft, or the published one to fork).
	const versions: FlowVersionOut[] = versionsRes.ok ? await versionsRes.json() : [];
	const forms: FlowForm[] = formsRes.ok ? await formsRes.json() : [];
	const documents: FlowDocument[] = documentsRes.ok
		? itemsOf((await documentsRes.json()) as FlowDocument[] | ListingPage<FlowDocument>)
		: [];
	const templates: FlowTemplate[] = templatesRes.ok ? await templatesRes.json() : [];
	const terms: FlowTerm[] = termsRes.ok
		? ((await termsRes.json()) as TermListItem[]).map((t) => ({ uid: t.uid, title: t.title }))
		: [];
	const products: CatalogProduct[] = productsRes.ok ? await productsRes.json() : [];
	const roles: FlowRoleOption[] = rolesRes.ok
		? ((await rolesRes.json()) as (FlowRoleOption & { portal: string | null })[]).filter(
				(r) => r.portal !== 'client'
			)
		: [];
	// A journey cannot hand over to itself — that is a loop the publish validator refuses, so it is
	// not offered in the first place.
	const flows: FlowHandoffTarget[] = flowsRes.ok
		? ((await flowsRes.json()) as FlowOut[])
				.filter((f) => f.uid !== flow.uid)
				.map((f) => ({ uid: f.uid, code: f.code, name: f.name }))
		: [];
	const dryRunClients: DryRunClient[] = clientsRes.ok
		? itemsOf((await clientsRes.json()) as Client[] | ListingPage<Client>).map((c) => ({
				id: c.id,
				name: clientLabel(c)
			}))
		: [];
	return {
		flow,
		version: versions[0] ?? null,
		forms,
		documents,
		templates,
		terms,
		products,
		roles,
		flows,
		dryRunClients,
		canManage: can(locals.permissions, locals.role, 'flows:write')
	};
};

/** Save the definition as a draft version. Returns the saved FlowVersionOut. */
async function saveDraft(
	fetchFn: typeof globalThis.fetch,
	accessToken: string | null,
	uid: string,
	definition: unknown
): Promise<Response> {
	return fetchFn(`${apiUrl()}/flows/${uid}/versions`, {
		method: 'POST',
		headers: jsonHeaders(accessToken),
		body: JSON.stringify({ definition })
	});
}

export const actions: Actions = {
	/** Save the builder's definition as a draft (validated server-side; 422 → message). */
	save: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'flows:write')) {
			return fail(403, { error: 'You do not have permission to edit flows.' });
		}
		let definition: unknown;
		try {
			definition = JSON.parse(String((await request.formData()).get('definition') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed definition.' });
		}
		const res = await saveDraft(fetch, locals.accessToken, params.uid, definition);
		if (!res.ok) return fail(res.status, { error: await detail(res) });
		const version: FlowVersionOut = await res.json();
		return { ok: true, version };
	},

	/** Save the current definition, then publish the resulting version. */
	publish: async ({ params, request, locals, fetch }) => {
		if (!locals.user) return fail(401, { error: 'Not signed in.' });
		if (!can(locals.permissions, locals.role, 'flows:write')) {
			return fail(403, { error: 'You do not have permission to publish flows.' });
		}
		let definition: unknown;
		try {
			definition = JSON.parse(String((await request.formData()).get('definition') ?? '{}'));
		} catch {
			return fail(400, { error: 'Malformed definition.' });
		}
		const saveRes = await saveDraft(fetch, locals.accessToken, params.uid, definition);
		if (!saveRes.ok) return fail(saveRes.status, { error: await detail(saveRes) });
		const version: FlowVersionOut = await saveRes.json();
		const pubRes = await fetch(`${apiUrl()}/flows/${params.uid}/versions/${version.uid}/publish`, {
			method: 'POST',
			headers: authHeaders(locals.accessToken)
		});
		if (!pubRes.ok) return fail(pubRes.status, { error: await detail(pubRes) });
		return { ok: true, published: true };
	},

	/**
	 * THE DRY RUN, from the builder's drawer. Performs nothing (`$lib/flow-dry-run` and the API
	 * endpoint's docstring), and deliberately does not save: the drawer sends the canvas's CURRENT
	 * definition in the form, so an author tests the graph on screen rather than the last version
	 * they happened to save. Shared verbatim with `/flows/[uid]/dry-run`, so the two mounts cannot
	 * drift.
	 */
	dryRun: async ({ params, request, locals, fetch }) =>
		runDryRun(fetch, locals, params.uid, await request.formData())
};
