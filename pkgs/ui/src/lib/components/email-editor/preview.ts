/** Shared email-preview fetching — the request contract, the error derivation and the monotonic sequence
 *  guard. One copy, because the stale guard only bites under a slow network, where a second would not. */

export interface EmailPreview {
	subject: string;
	preheader: string;
	/** Merged + CSS-inlined body. NOT sanitised — render only in a `sandbox=""` iframe, no allow-scripts. */
	html: string;
	/** Tokens the body used that this code's send site does not provide — they rendered empty. */
	missing: string[];
}

/** Whose brand an email is rendered in — the API's `Audience` mirrored; it also picks a test send's identity. */
export type PreviewAudience = 'client' | 'staff';

export interface PreviewPayload {
	subject: string;
	preheader: string;
	html: string;
	css: string;
	/** Required, not optional: an omitted audience silently takes the API's `client` default, rendering a
	 *  staff template in the wrong brand. The test sender takes this type too, so the two cannot disagree. */
	audience: PreviewAudience;
}

/** The audience each code's REAL send site dresses it in — a starting position the author can flip, read
 *  off those send sites because the brand is a send-time fact (`email.brand_for(..., portal=)`).
 *
 *  For the four notification codes the decider is the RULE, not the code. All four are rendered by the
 *  notification email subscriber, which resolves `portal = "client" if rule.portal == "client" else
 *  "platform"` (`notifications/email_consumer.py`), and `rule.portal` is
 *  `catalogue.audience_portal(audience_key)` — `client` only for the audience keyed literally `client`.
 *  So the two document codes ship on a `client` audience, and the two task codes on the `assignee` one,
 *  which is EVERY kind of assignee including a contact: a task notice is staff-branded even when it
 *  reaches a client, because the audience carries the portal and the catalogue defines no client-keyed
 *  task audience to carry the other one. A firm may also point one of its own rules at any
 *  notification-category code (`email_catalog.notification_email_codes`), so what is listed here is the
 *  audience the SHIPPED rule renders in, never a property of the code itself.
 *
 *  `invite`/`password_reset` are genuinely either (`provisioning.py` the portal GRANTED,
 *  `routers/internal.py` the posting host), so `client` there is only the larger population — as it is
 *  for an unknown code, and for `notification`: that wrapper dresses every rule's notice on both
 *  portals, so it is deliberately absent below rather than given an entry no send could bear out. */
const AUDIENCE_BY_CODE: Readonly<Record<string, PreviewAudience | undefined>> = {
	invite: 'client',
	password_reset: 'client',
	document_sent: 'client',
	request_raised: 'client',
	task_assigned: 'staff',
	task_reminder: 'staff'
};

export function defaultAudienceFor(code: string | null | undefined): PreviewAudience {
	if (!code) return 'client';
	return AUDIENCE_BY_CODE[code] ?? 'client';
}

/** The audience a request body claims, narrowed to what the API accepts.
 *
 *  Both apps' `preview` and `send-test` proxies assemble the outbound body field by field — the
 *  arrangement whose comment above records that `audience` once went missing from it entirely — so
 *  each had its own copy of this narrowing. One implementation means an audience added to
 *  `PreviewAudience` is handled in the place the type is declared, rather than defaulting to
 *  `client` in four route handlers that answer 200 while rendering the wrong brand. */
export function readAudience(value: unknown): PreviewAudience {
	return value === 'staff' ? 'staff' : 'client';
}

/** A settled preview request. `stale` means a newer request started in flight, so the caller drops it. */
export type PreviewResult =
	{ stale: true } | { stale: false; data: EmailPreview } | { stale: false; error: string };

/** A preview fetcher bound to one endpoint; the URL is passed in so the firm and operations surfaces
 *  share one contract and one stale guard. Each loader keeps its OWN sequence counter, so a response no
 *  longer the newest is `stale` rather than written over fresher content, and the payload posts as-is:
 *  enumerating keys is how a proxy silently drops one. */
export function createPreviewFetcher(
	previewUrl: string,
	fetchImpl: typeof fetch = fetch
): (payload: PreviewPayload) => Promise<PreviewResult> {
	let seq = 0;

	return async function load(payload: PreviewPayload): Promise<PreviewResult> {
		const mine = ++seq;
		try {
			const res = await fetchImpl(previewUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
			});
			if (mine !== seq) return { stale: true };
			if (!res.ok) return { stale: false, error: await errorMessage(res) };
			return { stale: false, data: (await res.json()) as EmailPreview };
		} catch (e) {
			if (mine !== seq) return { stale: true };
			return { stale: false, error: e instanceof Error ? e.message : 'Preview failed' };
		}
	};
}

/** SvelteKit's `error()` serialises as `{ message }`; fall back to the raw body, then to the status. */
async function errorMessage(res: Response): Promise<string> {
	const fromJson = await res
		.clone()
		.json()
		.then((b) => (b as { message?: string } | null)?.message)
		.catch(() => null);
	if (fromJson) return fromJson;
	const text = await res.text().catch(() => '');
	return text || `Preview failed (${res.status})`;
}
