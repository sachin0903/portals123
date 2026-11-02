import { redirect } from '@sveltejs/kit';
import { modelTenant } from '$lib/model';
import type { PageServerLoad } from './$types';

/**
 * The model's email templates moved to the shared per-tenant surface,
 * `/tenants/{uid}/email-templates` — the model is just the tenant whose published templates a new
 * firm clones, so it stopped being a page of its own. This redirect only exists so a link saved
 * while it WAS a page still lands somewhere useful; nothing in the app points here.
 *
 * The UID, not the serial id: the app's tenant routes are uid-addressed (the API's own routes are
 * the ones taking the serial), so redirecting with an id 404s. This was the bug
 * `src/routes/tenants/tenant-links.test.ts` exists to catch, and did.
 */
export const load: PageServerLoad = async ({ locals, fetch }) => {
	if (!locals.user) redirect(303, '/login');
	const uid = await modelTenant(locals.accessToken, fetch)
		.then((m) => m.uid)
		.catch(() => null);
	// A failed lookup means the model tenant is missing entirely, which is a /model problem, not a
	// redirect problem — send them there rather than to a tenant we could not resolve.
	redirect(308, uid === null ? '/model' : `/tenants/${uid}/email-templates`);
};
