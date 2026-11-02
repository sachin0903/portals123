/**
 * Dry-run a journey on its own page.
 *
 * The dry run's real home is a drawer inside the builder, where an author tests the graph they are
 * looking at without leaving it. This page exists because the same panel is worth having on its
 * own too: an author checking a PUBLISHED journey against a client who has just complained does
 * not want to open the editor to do it, and a reviewer with `flows:write` but no interest in the
 * canvas has somewhere to go.
 *
 * The action is `runDryRun` from `$lib/flow-dry-run`, shared with the builder's drawer so the two
 * mounts cannot behave differently.
 */
import { redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders, fetchJson, type Client } from '$lib/api';
import { itemsOf, type ListingPage } from '@graphene/ui/book/listing';
import { runDryRun } from '$lib/flow-dry-run';
import type { DryRunClient, FlowOut } from '@graphene/ui/book/journeys';
import type { Actions, PageServerLoad } from './$types';

/** The subject picker's list. Capped: a firm with ten thousand clients does not want them all in a
 *  dropdown, and the described-subject mode covers anyone not in the first slice. */
const MAX_CLIENTS = 200;

function label(client: Client): string {
	const person = [client.first_name, client.surname].filter(Boolean).join(' ').trim();
	return person || client.name || client.email || `Client ${client.id}`;
}

export const load: PageServerLoad = async ({ params, locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const canRun = can(locals.permissions, locals.role, 'flows:write');
	const headers = authHeaders(locals.accessToken);

	const [flowRes, clientsRes] = await Promise.all([
		fetchJson<FlowOut>(fetch, `${apiUrl()}/flows/${params.uid}`, headers),
		// RLS scopes this to the caller's own book, so the picker can only ever offer clients they
		// may already see. A failure just means the picker degrades to a record-id input.
		canRun
			? fetchJson<Client[] | ListingPage<Client>>(
					fetch,
					`${apiUrl()}/crm/contacts?limit=${MAX_CLIENTS}`,
					headers
				)
			: Promise.resolve({ ok: false as const, status: 403 })
	]);

	const clients: DryRunClient[] = clientsRes.ok
		? itemsOf(clientsRes.data).map((c) => ({ id: c.id, name: label(c) }))
		: [];

	return {
		flowId: params.uid,
		flow: flowRes.ok ? flowRes.data : null,
		clients,
		canRun
	};
};

export const actions: Actions = {
	/** THE DRY RUN. Performs nothing — see `$lib/flow-dry-run` and the API endpoint's docstring. */
	dryRun: async ({ params, request, locals, fetch }) =>
		runDryRun(fetch, locals, params.uid, await request.formData())
};
