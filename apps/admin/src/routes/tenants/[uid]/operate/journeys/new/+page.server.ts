import { fail, redirect } from '@sveltejs/kit';
import type { CatalogPlatform, CatalogProduct, FlowOut } from '@graphene/ui/book/journeys';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Client = {
	contact_id: number;
	name: string | null;
	email: string | null;
	type: 'individual' | 'organisation';
};

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [flows, clients, platforms, products] = await Promise.all([
		targetJson<FlowOut[]>(fetch, locals.accessToken, tenant.uid, '/flows', {
			status: 'published'
		}),
		targetJson<Client[]>(fetch, locals.accessToken, tenant.uid, '/documents/clients'),
		targetJson<CatalogPlatform[]>(fetch, locals.accessToken, tenant.uid, '/catalog/custodians'),
		targetJson<CatalogProduct[]>(fetch, locals.accessToken, tenant.uid, '/catalog/products')
	]);
	return {
		flows: flows.ok ? flows.data.filter((flow) => flow.status === 'published') : [],
		clients: clients.ok ? clients.data : [],
		platforms: platforms.ok ? platforms.data : [],
		products: products.ok ? products.data : [],
		loadError: !flows.ok || !clients.ok || !platforms.ok || !products.ok,
		writing
	};
};

export const actions: Actions = {
	default: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const contactId = Number(form.get('contact_id'));
		const platformId = Number(form.get('platform_id'));
		const productId = Number(form.get('product_id'));
		const flowCode = String(form.get('flow_code') ?? '');
		const productType = String(form.get('product_type') ?? '');
		if (![contactId, platformId, productId].every(Number.isInteger) || !flowCode || !productType)
			return fail(400, { error: 'Choose a flow, client, platform and product.' });
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/applications',
			{
				method: 'POST',
				body: JSON.stringify({
					flow_code: flowCode,
					platform_id: platformId,
					accounts: [
						{
							product_type: productType,
							product_id: productId,
							owners: [{ contact_id: contactId, role: 'owner' }]
						}
					],
					intake: {}
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		const created = await response.json();
		redirect(303, `/tenants/${event.params.uid}/operate/journeys/${created.execution_uid}`);
	}
};
