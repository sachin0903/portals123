import { error, redirect } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import { apiUrl, authHeaders } from '$lib/api';
import type { IllustrationModel } from '$lib/illustrations/form';
import type { PageServerLoad } from './$types';

/** The firm's models, as the form's picker needs them — declared once in `$lib/illustrations/form`
    beside the field set, and re-exported here because this route's callers import it from the load.
    Both numbers on it are assumptions the projection is built on, not decoration: CTC builds the
    growth from `target_return` (so a model without one cannot be illustrated against, and the
    picker has to say so before the adviser submits) and runs it net of `model_charge`. */
export type { IllustrationModel };

// Guards are per-page in this app (the root layout only surfaces the user), so this route needs its
// own — without it the composer renders to anyone who knows the URL. The client record and its
// pension accounts come from the `[id]` layout load via `data.detail`, so this route adds only the
// guard; the generate POST is answered by the parent `illustrations/+server.ts`.
//
// illustrations:write (the six-splits re-key: split out of documents:write — generating calls a
// paid external engine, and the API's create endpoints demand the same slug). The listing next
// door stays readable on documents:read; only the composer takes the write.
const ILLUSTRATION_TYPES = ['SIPP', 'GIA', 'ISA'] as const;
export type IllustrationType = (typeof ILLUSTRATION_TYPES)[number];

export const load: PageServerLoad = async ({ locals, fetch, url, params }) => {
	if (!locals.user) redirect(303, '/login');
	if (!can(locals.permissions, locals.role, 'illustrations:write')) {
		error(403, 'You do not have permission to generate illustrations.');
	}
	const type = url.searchParams.get('type')?.toUpperCase();
	if (!ILLUSTRATION_TYPES.includes(type as IllustrationType)) {
		redirect(303, `/clients/${params.uid}/illustrations`);
	}
	// The 403 is resolved here; the models are STREAMED so the composer's shape is on screen on
	// the click. An empty list is a real state — a firm with no models configured cannot
	// illustrate — so a failed read is not special-cased.
	const capability = await fetch(`${apiUrl()}/crm/illustration-capability`, {
		headers: authHeaders(locals.accessToken)
	});
	const setup = capability.ok
		? ((await capability.json()) as {
				enabled: boolean;
				platform_fees: { id: number; name: string; fee_type: string; summary: string }[];
				missing_disclosure: string[];
			})
		: { enabled: false, platform_fees: [], missing_disclosure: [] };
	return {
		illustrationType: type as IllustrationType,
		models: loadModels(locals.accessToken, fetch),
		wrappersEnabled: setup.enabled,
		platformFees: setup.platform_fees,
		missingDisclosure: setup.missing_disclosure
	};
};

async function loadModels(accessToken: string | null, fetch: typeof globalThis.fetch) {
	const res = await fetch(`${apiUrl()}/crm/models`, { headers: authHeaders(accessToken) });
	return (res.ok ? await res.json() : []) as IllustrationModel[];
}
