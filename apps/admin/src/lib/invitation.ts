/**
 * The ephemeral half of "reveal a member's invitation link".
 *
 * A set-password URL is a live credential: whoever holds it chooses that person's password. The API
 * side is already deliberate about it — its own audited endpoint (`POST …/members/{id}/invitation-
 * link`), never returned by the ordinary invite/resend responses. The page was the remaining leak:
 * it printed the URL into a read-only field that stayed on screen for as long as the operator left
 * the tab open, so a screenshot, a screen-share or a shoulder gets the credential.
 *
 * So the panel never displays it. The link goes to the clipboard on request and the page's copy is
 * dropped the moment it is copied, or when this TTL runs out, whichever comes first. The clipboard
 * is the destination the operator actually wants (they paste it into a call or a message), and the
 * countdown is honest about the page not keeping it.
 *
 * These are plain functions so the expiry, the countdown and the clipboard failure path can be
 * tested without a browser — the failure path matters, because with the URL never rendered a copy
 * that silently did nothing would leave the operator with no link and no way to tell.
 */

/** How long a revealed link stays available to copy before the page drops it. */
export const REVEAL_TTL_MS = 60_000;

export type Reveal = {
	url: string;
	/** The member the link belongs to, so the panel can name them. */
	userId: string | null;
	/** Epoch ms after which the page must forget the url. */
	expiresAt: number;
};

export function startReveal(url: string, userId: string | null, now = Date.now()): Reveal {
	return { url, userId, expiresAt: now + REVEAL_TTL_MS };
}

/** Milliseconds left before the reveal must be dropped (never negative). */
export function remainingMs(reveal: Reveal, now = Date.now()): number {
	return Math.max(0, reveal.expiresAt - now);
}

export function isExpired(reveal: Reveal, now = Date.now()): boolean {
	return remainingMs(reveal, now) === 0;
}

/** `m:ss`, rounded up so the last second reads "0:01" rather than "0:00" for a whole second. */
export function countdown(ms: number): string {
	const total = Math.ceil(ms / 1000);
	const minutes = Math.floor(total / 60);
	const seconds = total % 60;
	return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

/**
 * Put text on the clipboard. Returns false when the browser refuses or has no clipboard API
 * (a non-secure context, or permission denied) — the caller must then say so and keep the reveal,
 * because the operator has nothing else to fall back on.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
	try {
		const clipboard = globalThis.navigator?.clipboard;
		if (!clipboard?.writeText) return false;
		await clipboard.writeText(text);
		return true;
	} catch {
		return false;
	}
}
