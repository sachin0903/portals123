/**
 * What an image SOURCE is, and whether an inbox will ever draw it.
 *
 * The rule the whole picker is built on: a mail client fetches the image itself, with no credential
 * of ours, whenever the recipient happens to open the mail. So the only source that works is an
 * absolute https URL that anyone can GET — which is why the library reads the tenant bucket's
 * `files/tenant/` prefix (its only anonymous-read one) and nothing else.
 *
 * Two sources look fine on the canvas and ship dead:
 *   * a `data:` URI — GrapesJS's own asset manager produces one from a local file, and mail clients
 *     block inline images (`app/email/transport.py::masthead_image` refuses one for the same reason);
 *   * a relative path — resolvable against the editor's own page and nothing else.
 *
 * ONE RELATIVE PATH IS THE EXCEPTION, and it is the one the firm's own library publishes:
 * `/_assets/{name}`, the same-origin relay every asset in the product is served by. It looks like
 * the dead case and is not, because the SEND path states a host in front of it
 * (`app/email/document.py::to_email_html` → `storage.absolute_asset_urls`). Telling an author their
 * own uploaded logo "resolves only in this editor" would be false, and would send them looking for
 * a public URL for a picture that already works.
 *
 * Pure, so the rules are unit-tested rather than inferred from a rendered panel.
 */

export type ImageSourceKind =
	'none' | 'hosted' | 'relay' | 'insecure' | 'embedded' | 'merge' | 'relative';

/**
 * The firm's own asset relay: `/_assets/{kind}-{16 hex}.{ext}`.
 *
 * MIRRORS `app/storage.py`'s `PUBLIC_ASSET_PREFIX` + `_PUBLIC_ASSET_NAME`, which is the source of
 * truth — the API refuses to serve a name outside that grammar and the send path refuses to make
 * one absolute, so a path this pattern accepts and Python's does not would be called usable here
 * and arrive broken. `source.test.ts` pins the shape; keep the two in step.
 */
const RELAY_PATH = /^\/_assets\/[A-Za-z0-9-]+-[0-9a-f]{16}\.(?:svg|png|jpg|webp|ico)$/;

/** GRAPESJS'S OWN PLACEHOLDER, which is a raw `<svg …>` STRING it puts in the model's `src` for an
 *  image that has none. Two problems, not one: it showed as markup in the Source field, and an author
 *  who never chose a picture would PUBLISH `src="<svg …>"` — a broken image in every inbox. Treated as
 *  no source at all, so the field is empty and the hint says an image has not been chosen.
 *
 *  Matched on the shape rather than the exact string: it is GrapesJS's asset, not ours, and a bump that
 *  changes its viewBox must not turn the check off silently. */
export function isPlaceholderSource(src: string): boolean {
	return /^\s*<svg[\s>]/i.test(src);
}

/** A merge token — `{{ logo_url }}` — whose value the send path fills in. */
export function isMergeSource(src: string): boolean {
	const s = src.trim();
	return s.includes('{{') && s.includes('}}');
}

export function classifyImageSource(src: string): ImageSourceKind {
	const s = src.trim();
	if (!s || isPlaceholderSource(s)) return 'none';
	// Checked before the scheme tests: a token can legitimately sit inside a URL
	// (`https://cdn/{{ logo }}.png`), and the token is what decides at send.
	if (isMergeSource(s)) return 'merge';
	if (/^data:/i.test(s)) return 'embedded';
	// Before the generic relative case, and only for the exact relay grammar: any OTHER relative
	// path really does resolve here and nowhere else.
	if (RELAY_PATH.test(s)) return 'relay';
	if (/^https:\/\//i.test(s)) return 'hosted';
	if (/^http:\/\//i.test(s)) return 'insecure';
	return 'relative';
}

/** Whether a recipient's mail client can be expected to draw it. */
export function isUsableInEmail(src: string): boolean {
	const kind = classifyImageSource(src);
	return kind === 'hosted' || kind === 'relay' || kind === 'merge';
}

/**
 * Whether an image that is ALREADY a resolved URL will arrive — an absolute https one, or the
 * firm's own relay path, which the send path makes absolute.
 *
 * `isUsableInEmail`'s sibling, and narrower by one case: a merge token is usable in a TEMPLATE
 * (the send fills it in) and is not an answer about a picture somebody is holding. The brand
 * resolvers hold resolved branding values, so this is their question.
 */
export function isResolvedMailableImage(src: string | null | undefined): boolean {
	const kind = classifyImageSource(typeof src === 'string' ? src : '');
	return kind === 'hosted' || kind === 'relay';
}

/**
 * A firm's relay path, qualified by WHOSE it is — for a surface serving a tenant that is not the
 * one the request host names.
 *
 * A stored logo is `/_assets/{name}` and nothing more, because the relay resolves the tenant from
 * the host it was called on: right on a firm's own portal, and wrong on the internal-operations
 * one, where the host names the OPERATIONS tenant. An operator opening a firm's email template
 * therefore asked Graphene's bucket for that firm's object and got a 404 — a broken image with no
 * fallback behind it, because the canvas suppresses the wordmark whenever a URL is present. The
 * operations app carries a second relay route keyed by tenant for exactly this, which until now had
 * no caller.
 *
 * Anything that is not the relay grammar is returned UNCHANGED: an absolute URL needs no host to be
 * stated against, a `data:` URI carries its own bytes, and a merge token is not a URL at all.
 * `tenantUid` missing likewise returns the value untouched rather than building `/_assets//{name}`.
 */
export function tenantRelayPath(
	src: string | null | undefined,
	tenantUid: string | null | undefined
): string | null {
	const value = typeof src === 'string' ? src.trim() : '';
	const uid = typeof tenantUid === 'string' ? tenantUid.trim() : '';
	if (!value) return null;
	if (!uid || !RELAY_PATH.test(value)) return value;
	return `/_assets/${uid}/${value.slice('/_assets/'.length)}`;
}

/**
 * One line under the control saying what the current source IS — a compact stamp for a working
 * source, and the reason plus the fix for one that will not arrive.
 */
export function describeImageSource(src: string): string {
	switch (classifyImageSource(src)) {
		case 'none':
			return 'No image chosen.';
		case 'merge':
			return 'Filled in at send.';
		// The relay says the same thing as a hosted URL — its file name. An author does not need to
		// know how their firm's images are served, only which picture this is.
		case 'relay':
		case 'hosted':
			return fileNameOf(src);
		case 'insecure':
			return 'Served over http, which many mail clients block. Use an https link.';
		case 'embedded':
			return 'Embedded in the template, which mail clients do not render. Choose a hosted image.';
		case 'relative':
			return 'A relative path resolves only in this editor. Use a full https link.';
	}
}

/**
 * What to CALL the picture: its file name, or — when the last segment carries no extension, so it
 * names a route rather than a file — the host with it. `…/300x90/png` on its own reads as "png",
 * which tells an author nothing about which image they are looking at.
 */
export function fileNameOf(src: string): string {
	const trimmed = src.trim();
	const path = trimmed.split(/[?#]/)[0] ?? '';
	const segments = path.replace(/\/+$/, '').split('/');
	const raw = segments.pop() ?? '';
	let last = raw;
	try {
		last = decodeURIComponent(raw);
	} catch {
		// A stray `%` is not an error worth reporting; the raw segment is still the better label.
	}
	if (!last) return trimmed;
	if (last.includes('.')) return last;
	const host = /^https?:\/\/([^/]+)/i.exec(trimmed)?.[1]?.replace(/^www\./i, '');
	return host ? `${host}/${last}` : last;
}

/** Whether a pasted link may be accepted, and what to say when it may not. */
export function checkPastedLink(value: string): { ok: boolean; message: string } {
	const kind = classifyImageSource(value);
	if (kind === 'hosted' || kind === 'relay' || kind === 'merge') return { ok: true, message: '' };
	if (kind === 'none') return { ok: false, message: '' };
	return { ok: false, message: describeImageSource(value) };
}

/** One image in the firm's library, as the API publishes it. */
export type LibraryImage = {
	key: string;
	url: string;
	label: string;
	size: number;
	last_modified?: string | null;
};

export type ImageLibrary = {
	images: LibraryImage[];
	next_token?: string | null;
	/**
	 * Whether the firm has somewhere to put an image that a mail client could then fetch. False ⇒ an
	 * upload would land as a `data:` URI, so the picker offers a link instead of an Upload that
	 * produces something an inbox will not draw.
	 *
	 * A BUCKET is now the whole requirement — the relay serves the object and needs no
	 * browser-reachable base URL — so this is false only where object storage is unconfigured
	 * altogether.
	 */
	hosted: boolean;
};
