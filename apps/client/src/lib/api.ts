import { env } from '$env/dynamic/private';

/** Base URL of the FastAPI backend (server-side calls). */
export const apiUrl = (): string => env.API_URL ?? 'http://127.0.0.1:8000';

/** Bearer auth header for the signed-in user's access token. */
export const authHeaders = (accessToken: string | null): Record<string, string> => ({
	Authorization: `Bearer ${accessToken}`
});

export const jsonHeaders = (accessToken: string | null): Record<string, string> => ({
	...authHeaders(accessToken),
	'Content-Type': 'application/json'
});

/**
 * A public id as it may appear in an API path, or null.
 *
 * Every form action that puts a client-supplied `uid` into an upstream URL goes through this
 * first. Node's `fetch` parses the string it is given, so a raw form field of
 * `x/../../messaging/thread/messages?` dropped the intended `/complete` suffix and retargeted
 * the POST — still carrying the caller's own bearer — at whichever API route the caller named.
 * Same-origin only (SvelteKit's origin check, a Lax cookie), but an open proxy as yourself.
 *
 * The shape is the ONE the platform mints (`app/ids.py`: 12 alphanumerics, Decision 9) widened
 * to the legacy prefixed rows that were never rewritten (`seccl_…`, `con_…`, `acc_…`) — a closed
 * character set with no `/`, `?`, `#`, `%`, `&` or `.`, so the value cannot escape its path
 * segment however it is spelled. Length-capped: a uid is not a payload.
 */
export function publicId(raw: unknown): string | null {
	if (typeof raw !== 'string') return null;
	const value = raw.trim();
	return /^[A-Za-z0-9_-]{1,64}$/.test(value) ? value : null;
}

/** Result of a load()-time GET: parsed data on success, or the failing status so the caller can
 * surface a retry banner instead of substituting fake-empty data. */
export type LoadResult<T> = { ok: true; data: T } | { ok: false; status: number };

/** Backoff (ms) before each automatic retry; its length sets the retry count (here: 2 retries,
 * so 3 attempts total). */
const RETRY_BACKOFF_MS = [150, 400];

/** Transient statuses worth retrying: a network error (status 0) or a 401. 5xx fail fast so a
 * struggling API does not add retry backoff to every click. Deterministic 4xx fail fast too. */
const isTransient = (status: number): boolean => status === 0 || status === 401;

const FETCH_TIMEOUT_MS = 12_000;

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** GET + parse JSON for a page load, distinguishing a genuine failure from a legitimately empty
 * result. Retries transient failures a couple of times with a short backoff before giving up; the
 * caller surfaces a Retry banner on persistent failure (its Retry re-runs load through a fresh
 * request, where hooks.server.ts refreshes an expired token). Never collapses a non-OK response
 * to an empty array (the old `res.ok ? json : []` masked transient failures as "no data"). */
export async function fetchJson<T>(
	fetch: typeof globalThis.fetch,
	url: string,
	headers: Record<string, string>
): Promise<LoadResult<T>> {
	const attempts = RETRY_BACKOFF_MS.length + 1;
	for (let attempt = 0; attempt < attempts; attempt++) {
		if (attempt > 0) await delay(RETRY_BACKOFF_MS[attempt - 1]);
		const last = attempt === attempts - 1;
		try {
			const res = await fetch(url, { headers, signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
			if (res.ok) return { ok: true, data: (await res.json()) as T };
			if (!isTransient(res.status) || last) {
				console.error(`load fetch failed: ${url} → ${res.status}`);
				return { ok: false, status: res.status };
			}
			console.warn(
				`load fetch ${res.status} on ${url} — retrying (attempt ${attempt + 1}/${attempts})`
			);
		} catch (err) {
			if (last) {
				console.error(`load fetch errored: ${url}`, err);
				return { ok: false, status: 0 };
			}
			console.warn(`load fetch errored on ${url} — retrying (attempt ${attempt + 1}/${attempts})`);
		}
	}
	return { ok: false, status: 0 };
}

/** Return deliberate validation guidance without exposing backend diagnostics. */
export async function detail(res: Response): Promise<string> {
	try {
		const body = await res.json();
		const raw =
			typeof body?.detail === 'string'
				? body.detail
				: Array.isArray(body?.detail) && body.detail[0]?.msg
					? String(body.detail[0].msg)
					: '';
		const message = raw.replace(/^value error,\s*/i, '').trim();
		const looksInternal =
			/(api|http|status|workos|identity provider|portal|session|traceback|exception|sql|\/[a-z][\w/-]*)/i.test(
				message
			);
		if ([400, 409, 422].includes(res.status) && message && !looksInternal) return message;
	} catch {
		/* fall through */
	}
	return 'We couldn’t complete that request. Please try again.';
}

/** An account the signed-in client owns, from GET /crm/accounts (RLS-scoped to them). */
export type Account = {
	id: number;
	/** NOT NULL since `0111_accounts_uid_not_null` — it is the account's address. */
	uid: string;
	name: string | null;
	status: string;
	currency: string | null;
	value: number | null;
	contact_id: number | null;
	risk_level: string | null;
	custody: string | null;
	/** Movement over the account's tracked value series — the SAME figure the accounts listing
	 *  shows, computed once server-side (`crm.py::_series_return`). `gain_pct` is a fraction, and
	 *  is null when there is no series to stand on: no basis is not a flat year. Do NOT derive a
	 *  return from the holdings' book cost here — that is what made one account read -11.6% on
	 *  the listing and +856.3% on its own record. */
	gain?: number | null;
	gain_pct?: number | null;
	/** The account's own value series, in `/crm/home`'s `performance` shape, so its page can wear
	 *  the portfolio header scoped to this one account. Empty without a series. */
	performance?: { label: string; value: number }[];
	performance_period?: { from: string; to: string } | null;
	/** The account's valuation date (`_VALUED_AT`), date-only ISO. */
	value_as_of?: string | null;
	opened_at: string | null;
	product_name: string | null;
	product_type: string | null;
	/** The account's own service: captured `investment_capacity`, else `discretionary` when a
	 *  model is on it, else absent. `execution_only` is what unlocks self-serve money on THIS
	 *  account, whatever book the person as a whole is in. */
	service_type?: string | null;
	/** Cash at the custodian, from the lake. Scheme cash is in `provider_positions`. */
	cash?: number | null;
	/** Unrealised gain/loss on the current holdings (value less book cost), from the lake — the
	 *  figure the platform's account record prints as "Unrealised gain / loss". Distinct from
	 *  `gain`, which is the movement of the value series. */
	growth?: number | null;
	provider_positions?:
		| {
				source: string;
				cash: number | null;
				as_of: string | null;
				crystallised: number | null;
				uncrystallised: number | null;
		  }[]
		| null;
	/** ISA/JISA subscription, as the custodian states it. */
	allowance_used?: number | null;
	allowance_limit?: number | null;
	allowance_remaining?: number | null;
	allowance_tax_year?: string | null;
};

/** A current holding (position) from the analytical lake, from GET /crm/holdings, tagged
 *  with its owning account. Values are normalised numbers.
 *
 *  `growth_pct` is the lake's `unrealised_gain_percent` AS THE PROVIDER STATES IT: a percent
 *  (12 = +12%), not a fraction, and not reliably scaled either — the API's `_holding_return`
 *  records reading one as e.g. +6,303%. This comment used to promise a fraction, and the
 *  account record believed it, which is why that page showed a dash in the Return column of
 *  every holding a client owns. Compute the return with `holdingReturn` ($lib/holdings), which
 *  derives it from `growth` and `book_cost` the way the API does. */
export type Holding = {
	account_id: number | null;
	account_name: string | null;
	account_external_id: string;
	asset_id: string | null;
	asset_name: string | null;
	isin: string | null;
	asset_type: string | null;
	value: number | null;
	quantity: number | null;
	price: number | null;
	book_cost: number | null;
	growth: number | null;
	growth_pct: number | null;
	allocation: number | null;
	twrr: number | null;
	as_of: string | null;
	account_uid?: string | null;
	lookthrough_uid?: string | null;
	lookthrough_fund_name?: string | null;
	lookthrough_as_of?: string | null;
	/** The allocation bucket (`crm.py::_asset_class`), the same one the donut is drawn from. */
	asset_class?: string | null;
};

/** A realised transaction from GET /crm/transactions, tagged with its owning account.
 *
 *  The listing projects `asset` and `subtype` (OLTP). Older lake-shaped keys (`asset_name`,
 *  `sub_type`, `movement_type`) are still accepted so composed-page fixtures keep compiling. */
export type Transaction = {
	account_id: number | null;
	account_name: string | null;
	account_external_id: string;
	date: string | null;
	type: string | null;
	subtype?: string | null;
	sub_type?: string | null;
	movement_type?: string | null;
	asset?: string | null;
	asset_name?: string | null;
	isin: string | null;
	amount: number | null;
	/** The API's signed figure (`statement.signed_amount`). Preferred by `signedAmount`. */
	signed_amount?: number | null;
	quantity: number | null;
	price: number | null;
	currency: string | null;
	status: string | null;
};
