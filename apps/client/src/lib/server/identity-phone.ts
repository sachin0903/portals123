/**
 * The phone half of an identity-photo link.
 *
 * The phone opens `/i/{code}`. This module spends that code — server to server, so the session
 * token never reaches the page as a script can read it — and then the photo page talks to the
 * API with the token from the cookie. What the API decides, and this module therefore does not:
 * whether the link is still live, and which photos it may send.
 */

import { apiUrl } from '$lib/api';
import { SUBMIT_FAILED, type IdentityCapture } from '$lib/identity-capture';

type Opts = { internalToken: string; fetch: typeof fetch };

function serviceHeaders(internalToken: string, token?: string): Record<string, string> {
	return {
		'x-internal-token': internalToken,
		...(token ? { 'x-identity-phone-token': token } : {})
	};
}

export type RedeemedPhoneLink = { token: string; secondsRemaining: number };

/**
 * Spend the one-time code. A second attempt with the same code gets nothing, so a link
 * forwarded after it has been opened is inert. Null whenever the exchange fails — used,
 * expired, or the portal cannot reach the API.
 */
export async function redeemPhoneLink(code: string, opts: Opts): Promise<RedeemedPhoneLink | null> {
	if (!code || !opts.internalToken) return null;
	try {
		const res = await opts.fetch(`${apiUrl()}/identity-phone/redeem`, {
			method: 'POST',
			headers: { ...serviceHeaders(opts.internalToken), 'content-type': 'application/json' },
			body: JSON.stringify({ code })
		});
		if (!res.ok) return null;
		const body = (await res.json()) as { token?: string; seconds_remaining?: number };
		if (!body.token || typeof body.seconds_remaining !== 'number') return null;
		return { token: body.token, secondsRemaining: body.seconds_remaining };
	} catch {
		return null;
	}
}

export type PhoneCaptureLoad =
	{ ok: true; capture: IdentityCapture } | { ok: false; ended: boolean };

/** The capture this session may photograph. `ended` is a link that is no longer live; anything
 *  else (the API unreachable) keeps the cookie so a refresh can try again. */
export async function loadPhoneCapture(token: string, opts: Opts): Promise<PhoneCaptureLoad> {
	if (!token || !opts.internalToken) return { ok: false, ended: true };
	try {
		const res = await opts.fetch(`${apiUrl()}/identity-phone/capture`, {
			headers: serviceHeaders(opts.internalToken, token)
		});
		if (res.status === 404) return { ok: false, ended: true };
		if (!res.ok) return { ok: false, ended: false };
		return { ok: true, capture: (await res.json()) as IdentityCapture };
	} catch {
		return { ok: false, ended: false };
	}
}

/** Send the photos and name the document, in one request (`form` carries `document_type` and a
 *  file per photo). The error string is the API's, or `SUBMIT_FAILED` when it could not be read. */
export async function submitPhoneCapture(
	token: string,
	form: FormData,
	opts: Opts
): Promise<string | null> {
	if (!token || !opts.internalToken) return 'This link has already been used or has expired.';
	try {
		// No content-type: fetch sets the multipart boundary.
		const res = await opts.fetch(`${apiUrl()}/identity-phone/submit`, {
			method: 'POST',
			headers: serviceHeaders(opts.internalToken, token),
			body: form
		});
		if (res.ok) return null;
		if (res.status === 503) return SUBMIT_FAILED;
		return (await errorOf(res)) ?? SUBMIT_FAILED;
	} catch {
		return SUBMIT_FAILED;
	}
}

/** Tell the API what the phone has taken so far (item keys) and the document chosen, so the
 *  computer waiting on it shows each photo arrive. No photo is sent. Best effort: a failure is
 *  only a list that catches up later. */
export async function reportPhoneTaken(
	token: string,
	keys: string[],
	documentType: string | null,
	opts: Opts
): Promise<void> {
	if (!token || !opts.internalToken) return;
	try {
		await opts.fetch(`${apiUrl()}/identity-phone/taken`, {
			method: 'POST',
			headers: { ...serviceHeaders(opts.internalToken, token), 'content-type': 'application/json' },
			body: JSON.stringify({ keys, document_type: documentType })
		});
	} catch {
		// The next change says it again.
	}
}

async function errorOf(res: Response): Promise<string | null> {
	try {
		const body = (await res.json()) as { detail?: unknown };
		return typeof body.detail === 'string' && body.detail ? body.detail : null;
	} catch {
		return null;
	}
}
