import { error, redirect } from '@sveltejs/kit';
import { clientName } from '@graphene/ui';
import { detailFromShell, type BookContactShell } from '$lib/book/api';
import { targetJson } from '$lib/operate';
import { clientSidebarFocus, operateBase } from '$lib/record-focus';
import type { LayoutServerLoad } from './$types';

export const load: LayoutServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant, writing } = await parent();
	const result = await targetJson<BookContactShell>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/crm/contacts/${params.clientUid}/shell`
	);
	if (!result.ok) {
		if (result.status === 404) error(404, 'Client not found.');
		error(result.status || 500, 'Could not load the client.');
	}
	const detail = detailFromShell(result.data);
	const name = clientName(detail.contact);
	// Every section is readable under `tenant_portal:read` — the operate layout already refused
	// anyone without it — so each one the platform gates on a firm permission is simply present.
	const sidebarFocus = clientSidebarFocus({
		prefix: operateBase(tenant.uid),
		uid: detail.contact.uid,
		name,
		email: detail.contact.email,
		canReadDocuments: true,
		canSeeIllustrations: true,
		canReadActivity: true,
		canReadCompliance: true,
		canReadJourneys: true,
		canReadMessages: true,
		chatsUnread: result.data.chats_unread ?? false
	});
	return { detail, clientName: name, writing, sidebarFocus };
};
