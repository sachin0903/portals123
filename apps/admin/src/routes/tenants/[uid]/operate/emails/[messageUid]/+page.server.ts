import { error, redirect } from '@sveltejs/kit';
import type { CommsMessage } from '@graphene/ui/comms';
import { targetJson } from '$lib/operate';
import type { PageServerLoad } from './$types';
export const load: PageServerLoad = async ({ locals, fetch, parent, params }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const r = await targetJson<CommsMessage>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/firm/comms/messages/${params.messageUid}`
	);
	if (!r.ok)
		error(
			r.status === 404 ? 404 : 502,
			r.status === 404 ? 'Message not found' : 'Could not load this message'
		);
	return { tenantUid: tenant.uid, message: r.data };
};
