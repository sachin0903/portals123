import { fail, redirect } from '@sveltejs/kit';
import type { ClientOption } from '@graphene/ui/book/documents';
import { detail } from '$lib/api';
import { canOperateWrite, readOnlyFailure, targetJson, targetMutation } from '$lib/operate';
import type { Actions, PageServerLoad } from './$types';

type Template = { uid: string; name: string; status: string };

export const load: PageServerLoad = async ({ locals, fetch, parent, url }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const selectedTemplate = url.searchParams.get('template') ?? '';
	const [templates, clients, variables] = await Promise.all([
		targetJson<Template[]>(fetch, locals.accessToken, tenant.uid, '/templates'),
		targetJson<ClientOption[]>(fetch, locals.accessToken, tenant.uid, '/documents/clients'),
		selectedTemplate
			? targetJson<{ variables: string[] }>(
					fetch,
					locals.accessToken,
					tenant.uid,
					`/templates/${selectedTemplate}/variables`
				)
			: null
	]);
	return {
		templates: templates.ok
			? templates.data.filter((template) => template.status === 'published')
			: [],
		clients: clients.ok ? clients.data : [],
		selectedTemplate,
		variables: variables?.ok ? variables.data.variables : [],
		loadError: !templates.ok || !clients.ok || (variables != null && !variables.ok)
	};
};

export const actions: Actions = {
	generate: async (event) => {
		if (!canOperateWrite(event.locals)) return readOnlyFailure();
		const form = await event.request.formData();
		const templateUid = String(form.get('template_uid') ?? '');
		const contactId = Number(form.get('contact_id'));
		if (!templateUid || !Number.isInteger(contactId))
			return fail(400, { error: 'Choose a template and client.' });
		const answers = Object.fromEntries(
			[...form.entries()]
				.filter(([key]) => key.startsWith('answer:'))
				.map(([key, value]) => [key.slice(7), String(value)])
		);
		const response = await targetMutation(
			event.fetch,
			event.locals.accessToken,
			event.params.uid,
			'/documents/generate',
			{
				method: 'POST',
				body: JSON.stringify({
					template_uid: templateUid,
					client_contact_id: contactId,
					name: String(form.get('name') ?? '').trim() || null,
					answers
				})
			}
		);
		if (!response.ok) return fail(response.status, { error: await detail(response) });
		const document = await response.json();
		redirect(303, `/tenants/${event.params.uid}/operate/documents/${document.uid}`);
	}
};
