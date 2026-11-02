// The request contract lives beside `createPreviewFetcher` so no surface reimplements it, and it takes `PreviewPayload` itself, which makes "a preview and a test send of the same canvas agree about `audience`" a type-level fact.
// The outcome → copy mapping is a pure function so it can be tested per branch: the API answers THREE outcomes on purpose, and `suppressed` — this deployment deliberately mails nobody — is neither a success to trust nor a delivery bug to hunt.
// There is deliberately NO recipient in the payload: the API resolves the address from the caller's own session and ignores anything else, so do not add an address input (`SendTestEmail` in `apps/api/app/routers/email_templates.py`).
import type { PreviewPayload } from './preview.js';

export interface TestSendOutcome {
	status: 'delivered' | 'suppressed' | 'failed';
	/** The caller's OWN address, echoed back — the only address in the response. */
	to: string;
	/** The delivered subject, carrying the `[TEST] ` marker, so the author knows what to look for. */
	subject: string;
	/** For 'suppressed', the SETTING that suppressed it; for 'failed', a short message. */
	detail?: string | null;
	/** Tokens that rendered empty — the same diagnostic the preview returns. */
	missing?: string[];
}

// `throttled` is split out of `error` because the rate limit is a normal consequence of iterating on a template, not a fault, and its answer is "use the preview".
export type TestSendResult =
	| { kind: 'sent'; outcome: TestSendOutcome }
	| { kind: 'throttled'; retryAfterSeconds: number | null }
	| { kind: 'error'; error: string };

export interface TestSendFeedback {
	tone: 'success' | 'warning' | 'error';
	message: string;
}

// Bound to the app's own proxy, so the access token stays server-side. Unlike `createPreviewFetcher` there is NO stale-response guard: a superseded preview can be dropped because nothing happened, where every settled send has put mail on the wire and must be reported — the caller keeps a single in-flight flag instead.
export function createTestSender(
	sendTestUrl: string,
	fetchImpl: typeof fetch = fetch
): (payload: PreviewPayload) => Promise<TestSendResult> {
	// Posted whole, so `audience` — which decides the brand AND the envelope identity the test mail goes out as — travels without being named here. Still no recipient in it, and a test pins the exact key set.
	return async function send(payload: PreviewPayload): Promise<TestSendResult> {
		let res: Response;
		try {
			res = await fetchImpl(sendTestUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload)
			});
		} catch (e) {
			return { kind: 'error', error: e instanceof Error ? e.message : 'The test email failed.' };
		}
		if (res.status === 429) {
			return { kind: 'throttled', retryAfterSeconds: retryAfter(res) };
		}
		if (!res.ok) return { kind: 'error', error: await errorMessage(res) };
		return { kind: 'sent', outcome: (await res.json()) as TestSendOutcome };
	};
}

// Delta-seconds only: the API sends seconds, and a wrong clock would turn a 30-second wait into a nonsense figure, where a null simply drops the number from the sentence.
function retryAfter(res: Response): number | null {
	const raw = res.headers?.get?.('retry-after');
	if (!raw) return null;
	const seconds = Number(raw.trim());
	return Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : null;
}

/** SvelteKit's `error()` serialises as `{ message }`; the raw body then the status follow, so a proxy failure never surfaces as an empty toast. */
async function errorMessage(res: Response): Promise<string> {
	const fromJson = await res
		.clone()
		.json()
		.then((b) => (b as { message?: string } | null)?.message)
		.catch(() => null);
	if (fromJson) return fromJson;
	const text = await res.text().catch(() => '');
	return text || `The test email failed (${res.status}).`;
}

/** "45 seconds" / "2 minutes" — a bare seconds count reads oddly past a minute. */
function wait(seconds: number): string {
	if (seconds < 60) return `${seconds} second${seconds === 1 ? '' : 's'}`;
	const minutes = Math.ceil(seconds / 60);
	return `${minutes} minute${minutes === 1 ? '' : 's'}`;
}

// The one place a send outcome becomes words, and every branch says what to do next: where to look, which setting stopped it, or that the preview is the cheap way to keep iterating.
export function testSendFeedback(result: TestSendResult): TestSendFeedback {
	if (result.kind === 'throttled') {
		const when = result.retryAfterSeconds
			? `Try again in ${wait(result.retryAfterSeconds)}.`
			: 'Try again shortly.';
		return {
			tone: 'warning',
			message: `That’s a lot of test emails. ${when} The preview renders the same content without sending.`
		};
	}
	if (result.kind === 'error') return { tone: 'error', message: result.error };

	const { status, to, subject, detail, missing } = result.outcome;
	if (status === 'delivered') {
		const empty = missing?.length
			? ` These fields had no value and arrived empty: ${missing.join(', ')}.`
			: '';
		return { tone: 'success', message: `Sent to ${to} — look for “${subject}”.${empty}` };
	}
	if (status === 'suppressed') {
		// NOT a failure: the email rendered and the send ran, but sending is switched off (or this
		// address is not one it sends to). Saying so is what stops an author hunting a delivery fault.
		return {
			tone: 'warning',
			message: detail
				? `Not sent. ${detail} The email itself rendered correctly.`
				: 'Not sent, because email sending is switched off. The email itself rendered correctly.'
		};
	}
	return {
		tone: 'error',
		message: detail
			? `Couldn’t send the test email. ${detail}`
			: 'Couldn’t send the test email. The failure has been logged.'
	};
}
