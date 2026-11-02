import { fail, redirect } from '@sveltejs/kit';
import type { DryRunClient, FlowOut, FlowSimulation } from '@graphene/ui/book/journeys';
import { itemsOf, type ListingPage } from '@graphene/ui/book/listing';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Client = { id: number; first_name?: string; surname?: string; name?: string };
export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const [flow, clients] = await Promise.all([
		targetJson<FlowOut>(fetch, locals.accessToken, tenant.uid, `/flows/${params.flowUid}`),
		writing
			? targetJson<Client[] | ListingPage<Client>>(
					fetch,
					locals.accessToken,
					tenant.uid,
					'/crm/contacts',
					{ limit: 200 }
				)
			: null
	]);
	return {
		flowId: params.flowUid,
		flow: flow.ok ? flow.data : null,
		clients: clients?.ok
			? itemsOf(clients.data).map((client) => ({
					id: client.id,
					name:
						[client.first_name, client.surname].filter(Boolean).join(' ') ||
						client.name ||
						`Client ${client.id}`
				}))
			: ([] as DryRunClient[]),
		canRun: writing
	};
};

export const actions: Actions = {
	dryRun: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const kind = String(form.get('kind') ?? 'real') === 'synthetic' ? 'synthetic' : 'real';
		const subject: Record<string, unknown> = { kind };
		if (kind === 'real') {
			const contactId = Number(form.get('contact_id'));
			if (!Number.isInteger(contactId)) return fail(400, { dryRunError: 'Choose a client.' });
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
			{ method: 'POST', body: JSON.stringify({ subject }) }
		);
		if (!response.ok) return fail(response.status, { dryRunError: 'Could not simulate the flow.' });
		return { dryRun: (await response.json()) as FlowSimulation };
	}
};
