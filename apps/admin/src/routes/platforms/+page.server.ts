import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders } from '$lib/api';
import { can } from '$lib/permissions';
import type { PlatformStatus } from '$lib/platforms/registry';
import type { PageServerLoad } from './$types';

export type { PlatformStatus };

/**
 * The index cards, entirely from the API.
 *
 * They used to be a checked-in fixture module (`lib/platforms/data/*.ts`) whose own header said
 * "no API yet" — so every card's description and both its headline KPIs were invented, sitting
 * directly beneath a live green status badge. Seccl read "Webhook events today 12.4k", SEI "Rows
 * loaded 1.2M / Quarantined 12", Morgan Lloyd "Outbound queue depth 7". Nothing marked them as
 * concept data, and only `connection.status` was ever real.
 */
export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	// The nav hides Platforms without platforms:read, but a typed URL must be refused too —
	// the API gates each fetch regardless; this is the UX half (no half-rendered shell).
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');
	const res = await fetch(`${apiUrl()}/admin/platforms`, {
		headers: authHeaders(locals.accessToken)
	});
	const statuses: PlatformStatus[] = res.ok ? await res.json() : [];
	return { statuses };
};
