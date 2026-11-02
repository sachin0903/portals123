import { apiUrl } from '$lib/api';

/** A client group, as `GET /crm/groups` lists them. */
export type ClientGroupOption = {
	uid: string;
	name: string;
	slug: string;
	description?: string;
	sort_order?: number;
};

export type ClientGroupMembership = {
	groups: ClientGroupOption[];
	assignedGroups: ClientGroupOption[];
};

/**
 * The group's catalogue and the ones this client wears.
 *
 * Both reads are best-effort: a failure is an empty menu, never a failed record. The catalogue
 * is only asked for when the caller may read contacts.
 */
export async function loadClientGroups(
	uid: string,
	headers: HeadersInit,
	fetch: typeof globalThis.fetch,
	{ canList }: { canList: boolean }
): Promise<ClientGroupMembership> {
	const [listRes, assignedRes] = await Promise.all([
		canList ? fetch(`${apiUrl()}/crm/groups`, { headers }) : null,
		fetch(`${apiUrl()}/crm/contacts/${uid}/groups`, { headers })
	]);
	const listed: unknown = listRes?.ok ? await listRes.json() : [];
	const assigned = assignedRes?.ok
		? ((await assignedRes.json()) as { groups?: ClientGroupOption[] })
		: { groups: [] };
	return {
		groups: Array.isArray(listed) ? (listed as ClientGroupOption[]) : [],
		assignedGroups: Array.isArray(assigned.groups) ? assigned.groups : []
	};
}
