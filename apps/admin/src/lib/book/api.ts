/**
 * Fetch plumbing for the tenant-book section (`/tenants/[uid]/book`) — the waterfall's top tier.
 *
 * Graphene operations read a firm's book through the SAME staff-side endpoints the platform
 * portal uses, plus the operations-only `?tenant=<uid>` target (apps/api `app/db.py::book_session`:
 * requires operations scope + `book:read`, mints read-only claims FOR the target firm, audit keeps
 * naming the operator). EVERY call this section makes must carry the param — without it the API
 * resolves the OPERATIONS tenant's own (empty) book and the section would quietly render nothing,
 * which is why `bookUrl` is the only URL builder these loaders use.
 */
import type { ClientListRow, ClientRecordDetail, ScreeningCheck } from '@graphene/ui';
import { targetTenantUrl, type TargetParams } from '$lib/target-tenant';

/** A staff-side API URL targeted at the tenant's book: `${API}${path}?…&tenant=<uid>`.
 *
 *  Accepts the same `TargetParams` as `targetTenantUrl` (a plain record OR a `URLSearchParams`) —
 *  narrowing this to a record only, as it was, meant a caller already holding a `URLSearchParams`
 *  (e.g. `apiParams(query)` from a server-driven listing) had to unpack it back into an object
 *  first, for no reason: `targetTenantUrl` accepts both losslessly. */
export function bookUrl(path: string, tenantUid: string, params: TargetParams = {}): string {
	return targetTenantUrl(path, tenantUid, params);
}

export type BookResult<T> = { ok: true; data: T } | { ok: false; status: number };

/** GET + parse JSON, distinguishing a genuine failure from a legitimately empty result — the
 *  worst thing a book page can do is render "No clients yet" over a full firm because the API
 *  500'd. Leaner than the platform's `fetchJson` on purpose: no retry loop, because an ops
 *  proxy view surfaces a Retry banner and the operator clicks it (the platform retries because
 *  its users live in those pages all day). */
export async function getJson<T>(
	fetch: typeof globalThis.fetch,
	url: string,
	headers: Record<string, string>
): Promise<BookResult<T>> {
	try {
		const res = await fetch(url, { headers });
		if (!res.ok) {
			console.error(`book fetch failed: ${url} → ${res.status}`);
			return { ok: false, status: res.status };
		}
		return { ok: true, data: (await res.json()) as T };
	} catch (err) {
		console.error(`book fetch errored: ${url}`, err);
		return { ok: false, status: 0 };
	}
}

/** GET /crm/contacts as it responds now — the shared listing row plus the per-client value
 *  provenance the header stamps (mirrors the platform's `ClientRow`). */
export type BookClientRow = ClientListRow & {
	valued_account_count: number;
	value_as_of: string | null;
};

/** GET /crm/contacts/{id} — the slice the shared record renders plus the screening summary
 *  (`kyc` is crm.kyc_checks, the firm-facing view; hit detail stays in the /aml console). */
export type BookContactDetail = ClientRecordDetail & { kyc: ScreeningCheck[] };

/** `GET /crm/contacts/{uid}/shell` — chrome without the fat person record. */
export type BookContactShell = {
	contact: BookContactDetail['contact'];
	accounts: BookContactDetail['accounts'];
	advisers?: BookContactDetail['advisers'];
	users?: BookContactDetail['users'];
	kyc?: ScreeningCheck[];
	chats_unread?: boolean;
};

export function detailFromShell(shell: BookContactShell): BookContactDetail {
	return {
		contact: shell.contact,
		advisers: shell.advisers ?? [],
		users: shell.users ?? [],
		accounts: shell.accounts ?? [],
		notes: [],
		kyc: shell.kyc ?? []
	};
}

/**
 * `GET /documents?status=archived` — the fields the Deleted documents listing shows.
 *
 * A narrow structural pick of the API's `DocumentOut` rather than the generated type itself: this
 * page reads seven of its thirty-odd fields, and every one of the rest is about a LIVE document
 * (the per-viewer unread flags, the render error triple, the sent/delivery signals) and means
 * nothing here. Stating only what is rendered keeps the page honest about what it knows, and keeps
 * an unrelated `DocumentOut` field arriving or leaving from touching this section.
 */
export type BookArchivedDocument = {
	uid: string;
	name: string | null;
	document_type: string;
	source: string;
	/** Always `archived` for this listing (the API's word; the UI says "Deleted"). */
	status: string;
	size_bytes: number | null;
	created_at: string;
	/** The staffer who produced it, and the ONE client it was filed under. Either may be null. */
	created_by?: string | null;
	client?: string | null;
};
