import { error, redirect } from '@sveltejs/kit';
import { targetJson } from '$lib/operate';
import { can } from '$lib/permissions';
import { clientChecks } from '$lib/server/aml';
import type { PageServerLoad } from './$types';

/** What the firm itself sees: one status and a date per kind, never the result. */
type FirmScreening = {
	checks: Array<{
		kind: 'aml' | 'identity';
		status: 'not_started' | 'pending' | 'passed' | 'referred' | 'failed';
		updated_at: string | null;
	}>;
};

export const load: PageServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	const { tenant } = await parent();
	const result = await targetJson<FirmScreening>(
		fetch,
		locals.accessToken,
		tenant.uid,
		`/crm/contacts/${params.clientUid}/screening`
	);
	if (!result.ok) error(result.status || 500, 'Could not load compliance.');

	// Graphene's own row for the client is under aml:read. Without it the tab shows the firm's view
	// only; a failure to read it leaves the firm's view standing.
	const canReadAml = can(locals.permissions, locals.role, 'aml:read');
	const graphene = canReadAml
		? await clientChecks(fetch, locals.accessToken, tenant.uid, params.clientUid)
		: null;
	return { screening: result.data, graphene, canReadAml };
};
