/**
 * Which logo image(s) a Logo renders, given the tenant's branding assets.
 *
 * Extracted from logo.svelte as a pure function because the bug it encodes lived in a `$derived`
 * expression that no test could reach: the light source fell back from the icon asset to the full
 * logo, but the dark source was dropped unconditionally for the `mark` variant. A firm with
 * `logo_variant: 'icon'`, no icon asset and BOTH logos set — the stock Graphene tenant — rendered
 * its light logo in dark mode.
 *
 * The rule: the dark variant applies whenever we are showing the FULL LOGO, whichever variant was
 * asked for. It only drops out when a dedicated icon asset is actually in play, because the branding
 * model has no `icon_dark_url` — a firm's symbol has one form.
 */
export interface LogoAssets {
	/** `full` = mark + wordmark; `mark` = the symbol on its own. */
	variant?: 'full' | 'mark';
	/** The firm's full logo. */
	src?: string;
	/** Its dark-scheme counterpart. */
	darkSrc?: string;
	/** A dedicated compact symbol, so a firm can have a wordmark AND an icon. */
	iconSrc?: string;
}

export interface LogoSources {
	/** The image to render, or undefined ⇒ fall back to the built-in mark/lockup. */
	img?: string;
	/** The dark-scheme image, or undefined ⇒ no swap (one image serves both schemes). */
	dark?: string;
	/** True when a dedicated icon asset is in play (and so no dark swap exists). */
	usingIconAsset: boolean;
}

/**
 * Decoded size above which an inline raster is refused as a shell image. Mirrors
 * `MAX_INLINE_SHELL_BYTES` in the API's `app/shell_images.py`, which applies the same rule to the
 * branding payload before it reaches a portal; this is the second line, for a cached or stale
 * payload that still carries one.
 */
export const MAX_INLINE_SHELL_BYTES = 32 * 1024;

const INLINE_RASTER = /^data:image\/(?:png|jpeg|webp);base64,/i;

/**
 * The source the shell may paint, or `undefined` for an inline raster that is too big to be one.
 *
 * A `data:` PNG/JPEG/WebP in the staff shell is decoded on the main thread and becomes Largest
 * Contentful Paint — on staging a 3481×624 PNG in a 28px slot was the LCP on 29 of 30 pages, two
 * seconds after the page had otherwise finished. So anything over `MAX_INLINE_SHELL_BYTES` decoded
 * is dropped and Logo falls through to the built-in mark. URLs, proxy paths, SVG and small inline
 * images (icons, tests, a few KB) pass unchanged. Base64 is 4 characters per 3 bytes, so the size
 * is known without decoding.
 */
export function shellSafeSrc(src: string | undefined): string | undefined {
	if (!src || !INLINE_RASTER.test(src)) return src;
	const b64 = src.slice(src.indexOf(',') + 1);
	return Math.floor((b64.length * 3) / 4) > MAX_INLINE_SHELL_BYTES ? undefined : src;
}

export function logoSources({ variant = 'full', src, darkSrc, iconSrc }: LogoAssets): LogoSources {
	// Each asset is screened before the fallback rules run, so an oversized icon falls back to the
	// full logo and an oversized logo to the built-in mark — never to the bitmap.
	src = shellSafeSrc(src);
	darkSrc = shellSafeSrc(darkSrc);
	iconSrc = shellSafeSrc(iconSrc);
	const usingIconAsset = variant === 'mark' && !!iconSrc;
	return {
		img: usingIconAsset ? iconSrc : src,
		dark: usingIconAsset ? undefined : darkSrc,
		usingIconAsset
	};
}
