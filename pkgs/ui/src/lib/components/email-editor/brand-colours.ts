// A swatch writes the colour as a LITERAL, so a later rebrand leaves already-authored templates on the old colour; following one would need `{{ brand.accent }}`, which GrapesJS's `color` property type cannot round-trip (see the `colorPicker` comment in create.ts).
// Values arrive validated by `app/branding.py`, so this filters for PRESENCE, not shape — and stays pure, so the two portals cannot answer the question differently.
import type { Branding, BrandingColors } from '@graphene/auth';
import { isResolvedMailableImage } from './image-picker/source.js';

// A deliberate subset of the fourteen `BrandingColors` tokens — what an email is built out of, in swatch order. The rest are portal status tones.
export const EMAIL_SWATCH_KEYS: readonly (keyof BrandingColors)[] = [
	'brand', // the firm's accent — the reason this feature exists
	'brand_foreground', // text on the brand accent (a button on a brand fill)
	'primary', // the main action colour — the usual button fill
	'primary_foreground',
	'muted', // the quiet tint a section band or callout takes
	'background', // the email body surface
	'card', // a panel/section surface sitting on it
	'foreground', // body text
	'muted_foreground', // quiet copy — footers, small print
	'border' // hairlines between sections
];

// The LIGHT palette only: an email cannot switch scheme, so a dark-palette surface written as a literal would go out as a near-black "background" the author believed was theirs. Blanks and duplicates drop, and the empty array `createEmailEditor` reads as "no palette".
export function emailBrandColours(branding: Branding | null | undefined): string[] {
	const palette = branding?.colors?.light;
	if (!palette) return [];
	const out: string[] = [];
	// Case-insensitive, because CSS hex is: `#FFF` and `#fff` are one colour. The first spelling wins.
	const seen = new Set<string>();
	for (const key of EMAIL_SWATCH_KEYS) {
		const raw = (palette as Record<string, string | null | undefined>)[key];
		if (typeof raw !== 'string') continue;
		const value = raw.trim();
		if (!value) continue;
		const dedupe = value.toLowerCase();
		if (seen.has(dedupe)) continue;
		seen.add(dedupe);
		out.push(value);
	}
	return out;
}

// A MIRROR of the send path's own choice (`app/email/transport.py::_email_asset_urls`) — keep the two in step, because previewing a different picture from the one the send resolves is worse than previewing none. It reads `logo_variant`, falls back between wordmark and icon, and drops what an inbox will not fetch.
// NOT `logo-sources.ts::logoSources`: that resolves the portal shell's logo, which has a built-in Graphene mark to fall through to, where an email's only alternative is the firm's name set as type.
// A RELAY PATH COUNTS. `/_assets/{name}` is what every firm's stored logo now is, and the send path states the firm's host in front of it — so an https-only filter here reported "no logo" for every branded firm in the product, which is the exact fallback-to-type this whole area exists to avoid. `isResolvedMailableImage` is the one definition, shared with the image picker.
export function emailMastheadImage(branding: Branding | null | undefined): string | null {
	if (!branding) return null;
	// Mail clients block `data:` images, so an inlined one must fall through rather than arrive broken.
	const mailable = (url: string | null | undefined) => (isResolvedMailableImage(url) ? url! : null);
	const icon = mailable(branding.icon_url);
	const wordmark = mailable(branding.logo_url);
	return branding.logo_variant === 'icon' ? (icon ?? wordmark) : (wordmark ?? icon);
}

/* `emailBrandIcon` lived here, resolving `icon_url` for a second brand block to paint. Both are
   gone: there is ONE brand block, and `emailMastheadPreview` above already returns the mark for a
   firm whose `logo_variant` asks for it — the same choice the send makes. A separate resolver was
   how the two came to disagree about which image a firm's email wears. */

export interface MastheadPreview {
	src: string | null;
	mailable: boolean;
}

// ANY scheme, unlike the send: the canvas is a browser and renders a data URI perfectly, and a deployment with no object-store bucket inlines every upload, so https-only left the editor showing an empty box for the firm's own logo. `mailable` stops that becoming the opposite lie — the caller can say the inline logo will not go out.
export function emailMastheadPreview(branding: Branding | null | undefined): MastheadPreview {
	if (!branding) return { src: null, mailable: false };
	const pick = (url: string | null | undefined) =>
		typeof url === 'string' && url.trim() ? url.trim() : null;
	const icon = pick(branding.icon_url);
	const wordmark = pick(branding.logo_url);
	const src = branding.logo_variant === 'icon' ? (icon ?? wordmark) : (wordmark ?? icon);
	// `mailable` answers "will a recipient see this", so it has to accept the relay path too — it
	// said no for every firm whose logo is stored the way the product stores them, which would put a
	// "this will not go out" warning over a logo that does.
	return { src, mailable: isResolvedMailableImage(src) };
}
