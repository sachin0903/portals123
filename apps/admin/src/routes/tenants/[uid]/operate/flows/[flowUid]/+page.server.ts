import { fail, redirect } from '@sveltejs/kit';
import type {
	CatalogProduct,
	DryRunClient,
	FlowDocument,
	FlowForm,
	FlowOut,
	FlowSimulation,
	FlowTemplate,
	FlowVersionOut
} from '@graphene/ui/book/journeys';
import { itemsOf, type ListingPage } from '@graphene/ui/book/listing';
import {
	canOperateWrite,
	mutationFailure,
	readOnlyFailure,
	targetJson,
	targetMutation
} from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Role = { slug: string; name: string; portal?: string | null };
type Client = { id: number; first_name?: string; surname?: string; name?: string; email?: string };
const label = (client: Client) =>
	[client.first_name, client.surname].filter(Boolean).join(' ') ||
	client.name ||
	client.email ||
	`Client ${client.id}`;

export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const get = <T>(path: string) => targetJson<T>(fetch, locals.accessToken, tenant.uid, path);
	const [flow, versions, forms, documents, templates, terms, products, roles, flows, clients] =
		await Promise.all([
			get<FlowOut>(`/flows/${params.flowUid}`),
			get<FlowVersionOut[]>(`/flows/${params.flowUid}/versions`),
			get<FlowForm[]>('/forms'),
			get<FlowDocument[] | ListingPage<FlowDocument>>('/documents'),
			get<FlowTemplate[]>('/templates'),
			get<{ uid: string; title: string }[]>('/terms?status=published'),
			get<CatalogProduct[]>('/catalog/products'),
			get<Role[]>('/firm/roles'),
			get<FlowOut[]>('/flows'),
			get<Client[] | ListingPage<Client>>('/crm/contacts')
		]);
	const current = flow.ok ? flow.data : null;
	return {
		flow: current,
		version: versions.ok ? (versions.data[0] ?? null) : null,
		forms: forms.ok ? forms.data : [],
		documents: documents.ok ? itemsOf(documents.data) : [],
		templates: templates.ok ? templates.data : [],
		terms: terms.ok ? terms.data.map((t) => ({ uid: t.uid, title: t.title })) : [],
		products: products.ok ? products.data : [],
		roles: roles.ok ? roles.data.filter((role) => role.portal !== 'client') : [],
		flows: flows.ok && current ? flows.data.filter((row) => row.uid !== current.uid) : [],
		dryRunClients: clients.ok
			? itemsOf(clients.data)
					.slice(0, 200)
					.map((client) => ({ id: client.id, name: label(client) }))
			: ([] as DryRunClient[]),
		canManage: writing
	};
};

function definition(form: FormData): unknown | null {
	try {
		return JSON.parse(String(form.get('definition') ?? '{}'));
	} catch {
		return null;
	}
}

export const actions: Actions = {
	save: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const body = definition(await event.request.formData());
		if (body === null) return fail(400, { error: 'Malformed definition.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/flows/${event.params.flowUid}/versions`,
			{ method: 'POST', body: JSON.stringify({ definition: body }) }
		);
		if (!response.ok) return mutationFailure(response, 'Could not save the flow.');
		return { ok: true, version: await response.json() };
	},
	publish: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const body = definition(await event.request.formData());
		if (body === null) return fail(400, { error: 'Malformed definition.' });
		const saved = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/flows/${event.params.flowUid}/versions`,
			{ method: 'POST', body: JSON.stringify({ definition: body }) }
		);
		if (!saved.ok) return mutationFailure(saved, 'Could not save the flow.');
		const version = (await saved.json()) as FlowVersionOut;
		const published = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/flows/${event.params.flowUid}/versions/${version.uid}/publish`,
			{ method: 'POST' }
		);
		if (!published.ok) return mutationFailure(published, 'Could not publish the flow.');
		return { ok: true, published: true };
	},
	dryRun: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const draft = definition(form);
		if (draft === null) return fail(400, { error: 'Malformed definition.' });
		const kind = String(form.get('kind') ?? 'real') === 'synthetic' ? 'synthetic' : 'real';
		const subject: Record<string, unknown> = { kind };
		if (kind === 'real') {
			const raw = String(form.get('contact_id') ?? '').trim();
			const contactId = Number(raw);
			if (!raw || !Number.isInteger(contactId))
				return fail(400, { dryRunError: 'Choose a client.' });
			subject.contact_id = contactId;
		} else {
			subject.contact_type = String(form.get('contact_type') ?? 'individual');
			subject.custody = String(form.get('custody') ?? '') || null;
			subject.products = String(form.get('products') ?? '')
				.split(',')
				.map((value) => value.trim())
				.filter(Boolean);
		}
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			`/flows/${event.params.flowUid}/simulate`,
			{
				method: 'POST',
				body: JSON.stringify({ definition: draft, subject })
			}
		);
		if (!response.ok) return fail(response.status, { dryRunError: 'Could not simulate the flow.' });
		return { dryRun: (await response.json()) as FlowSimulation };
	}
};
