import { redirect } from '@sveltejs/kit';
import type { ApiConversation, ApiMessage } from '@graphene/ui/book/messages';
import { targetJson, targetMutation } from '$lib/operate';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { detail, tenant, writing } = await parent();
	let conversation: ApiConversation | null = null;
	if (writing) {
		const opened = await targetMutation(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/messaging/conversations',
			{ method: 'POST', body: JSON.stringify({ contact_id: detail.contact.id }) }
		);
		if (opened.ok) conversation = (await opened.json()) as ApiConversation;
	} else {
		const listed = await targetJson<ApiConversation[]>(
			fetch,
			locals.accessToken,
			tenant.uid,
			'/messaging/conversations',
			{ contact_id: String(detail.contact.id) }
		);
		conversation = listed.ok
			? (listed.data.find((item) => item.contact_id === detail.contact.id) ?? null)
			: null;
	}
	const messages = conversation
		? await targetJson<ApiMessage[]>(
				fetch,
				locals.accessToken,
				tenant.uid,
				`/messaging/conversations/${conversation.uid}/messages`
			)
		: null;
	return {
		conversation,
		messages: messages?.ok ? messages.data : [],
		loadError: conversation !== null && !messages?.ok,
		writing
	};
};
