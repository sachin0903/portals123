import { error, redirect } from '@sveltejs/kit';
import { apiUrl, authHeaders, detail } from '$lib/api';
import { can } from '$lib/permissions';
import type { LayoutServerLoad } from './$types';

/** One entity tab, as `GET /admin/platforms/{provider}/firms/{uid}/entities` describes it. */
export type FirmEntity = {
	key: string;
	label: string;
	relation: string;
	note: string;
	columns: string[];
	missing_columns: string[];
	/** `ok` | `not_built` | `unavailable` — never collapse these into "empty". */
	availability: string;
	reason?: string;
	rows?: number | null;
};

/**
 * The firm console's shell: which entities this platform publishes for this firm.
 *
 * Loaded in the LAYOUT rather than per entity page so the submenu is complete on first paint —
 * a nav that fills in per tab makes the set of entities look like it depends on which one you
 * are looking at.
 */
export const load: LayoutServerLoad = async ({ locals, params, fetch, parent }) => {
	if (!locals.user) redirect(303, '/login');
	// The parent layout already gates on platforms:read; re-checked because a layout's guard is
	// not inherited as an assertion, and this subtree reaches a firm's book.
	if (!can(locals.permissions, locals.role, 'platforms:read')) error(403, 'Not authorised');

	const provider = params.slug;
	// A concept platform has no warehouse to read, so there is nothing to show per firm. 404
	// rather than an empty console: the section does not exist for it.
	if (!provider) error(404, 'This platform has no live firm data');

	const { dataset } = await parent();
	const res = await fetch(`${apiUrl()}/admin/platforms/${provider}/firms/${params.uid}/entities`, {
		headers: authHeaders(locals.accessToken)
	});
	if (res.status === 404) error(404, `This firm is not enabled on ${provider}`);
	if (!res.ok) {
		// Reported, not swallowed to an empty list: "no entities" and "we could not ask" mean
		// opposite things, and only one is worth investigating.
		return {
			provider,
			uid: params.uid,
			firmName: null,
			sliceIds: [] as string[],
			entities: [] as FirmEntity[],
			loadError: await detail(res)
		};
	}
	const body = (await res.json()) as {
		slice_ids: string[];
		entities: FirmEntity[];
		actions?: number;
	};

	// The firm's name comes from the platform overview the parent layout already loaded, so this
	// costs no extra round trip.
	const firm = dataset?.firms?.enablements?.find((f) => f.uid === params.uid) ?? null;

	return {
		provider,
		uid: params.uid,
		firmName: firm?.name ?? null,
		sliceIds: body.slice_ids ?? [],
		entities: body.entities ?? [],
		// Whether this platform has any write actions at all. A provider with none must not
		// advertise an Actions page — an empty one reads as "the actions are broken" rather than
		// as "we cannot write to this custodian yet", which is the true and more useful answer.
		// Asked of the API rather than hard-coded, so adding a provider's actions lights the nav
		// up on its own instead of needing this list edited too.
		hasActions: (body.actions ?? 0) > 0,
		loadError: null
	};
};
