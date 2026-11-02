import { actionLogUntilBound } from '@graphene/ui';
import { PAGE_SIZE } from '@graphene/ui/book/listing';
import { fetchActionsPage } from '$lib/audit/actions-page';
import type { RequestHandler } from './$types';

/**
 * Same-origin "Load more" for the audit log — the DML record-change trail (`audit.log`), NOT the
 * action stream its four siblings page. Forwards the next keyset page (id < `before_id`) to the API
 * with the session token, carrying the same filters the trail is showing. The AuditLog shell appends
 * the result in place — no navigation, no growing `?before_id=` in the URL.
 *
 * It builds its own query on purpose: `GET /admin/audit` takes a different vocabulary
 * (`before_id`/`action`/`tenant_id`) from the action stream's, so `actionLogQuery` does not apply —
 * only the one rule the two trails genuinely share, the exclusive `until` bound, comes from
 * `@graphene/ui`. `list_audit` also carries no `has_more` flag, so `fetchActionsPage` falls back to
 * "a full page means there is more behind it" for this endpoint.
 *
 * What IS shared is the failure contract, via `fetchActionsPage`: a failure answers NON-2xx, never a
 * swallowed empty 200, so the trail keeps its "Load more" button rather than falsely showing "End of
 * the trail" during an outage — which would assert the whole compliance record-change trail had been
 * read when a request merely broke.
 *
 * `PAGE_SIZE` is the shared listing page size, so an appended page matches the loader's first one.
 */

/** Absent/blank ⇒ ABSENT from the query: `action=` is not "no action filter" at the API, it is a
 *  filter for the empty string. (`actionLogQuery`'s own rule, for this trail's param names.) */
function setIf(query: URLSearchParams, key: string, value: string | null): void {
	const trimmed = value?.trim();
	if (trimmed) query.set(key, trimmed);
}

/** Carries every value forward as its own repeated param, so "Load more" keeps the same OR. */
function appendAll(query: URLSearchParams, key: string, values: string[]): void {
	for (const value of values) {
		const trimmed = value.trim();
		if (trimmed) query.append(key, trimmed);
	}
}

export const GET: RequestHandler = async ({ url, locals, fetch }) => {
	const params = url.searchParams;
	const query = new URLSearchParams({ limit: String(PAGE_SIZE) });
	setIf(query, 'before_id', params.get('before_id'));
	setIf(query, 'action', params.get('action'));
	appendAll(query, 'actor', params.getAll('actor'));
	appendAll(query, 'tenant_id', params.getAll('tenant_id'));
	setIf(query, 'since', params.get('since'));
	// `until` is EXCLUSIVE at the API — send the end of the typed day, or the whole of that day is
	// missing from the answer, an omission that reads as "nothing was changed that day".
	setIf(query, 'until', actionLogUntilBound(params.get('until') ?? ''));
	return fetchActionsPage(fetch, '/admin/audit', query, locals);
};
