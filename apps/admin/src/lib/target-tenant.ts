import { apiUrl } from '$lib/api';

export type TargetParams =
	URLSearchParams | Record<string, string | number | boolean | null | undefined>;

/**
 * Build an API URL in a target firm's staff context.
 *
 * Every tenant-portal loader and action goes through this seam. Appending `tenant` last prevents a
 * caller-supplied value from replacing the route's firm, while accepting URLSearchParams keeps
 * listing filters lossless.
 */
export function targetTenantUrl(
	path: string,
	tenantUid: string,
	params: TargetParams = {}
): string {
	const search =
		params instanceof URLSearchParams
			? new URLSearchParams(params)
			: new URLSearchParams(
					Object.entries(params)
						.filter((entry): entry is [string, string | number | boolean] => entry[1] != null)
						.map(([key, value]) => [key, String(value)])
				);
	search.set('tenant', tenantUid);
	return `${apiUrl()}${path}?${search}`;
}
