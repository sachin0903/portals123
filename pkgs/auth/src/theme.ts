/**
 * Colour-scheme resolution — the pure half of the light/dark switcher.
 *
 * Three inputs decide which scheme a request renders in, in this order:
 *
 *   1. the user's explicit choice, in the `gp-theme` cookie (`light` | `dark` | `system`)
 *   2. the tenant's branding `mode` (`light` | `dark`; `auto` = no opinion)
 *   3. the OS `prefers-color-scheme`
 *
 * `system` stored in the cookie is itself an explicit choice — "follow my OS, ignore the firm's
 * default" — so it beats the tenant mode and lands on (3). No cookie at all means no choice, so the
 * tenant mode applies.
 *
 * The cookie is deliberately NOT httpOnly: the toggle writes it from the browser (no round trip) and
 * the server reads it to render the right scheme in the first paint. It carries a closed enum, never
 * user text, so there is nothing to escape and nothing to leak.
 *
 * Server-side, only (1) and (2) are knowable — the OS preference is not, unless the browser sends
 * the `Sec-CH-Prefers-Color-Scheme` client hint (which it only does after we have asked for it via
 * `Accept-CH`, i.e. from the second request on). So `resolveScheme` returns `null` when the answer
 * genuinely depends on the client, and `themeHead()` supplies a tiny blocking script that settles it
 * before the first paint. Both read the same two `<html>` attributes, so there is one source of
 * truth and the script, the server and the toggle can never disagree.
 *
 * Framework-agnostic and DOM-free on purpose: the SvelteKit `handle` hooks, the `ThemeToggle`
 * component and these tests all share it.
 */

/** The user's explicit choice. Not httpOnly — see the module note. */
export const THEME_COOKIE = 'gp-theme';
/** The class the design system's dark theme keys off (`.dark` in pkgs/ui theme.css). */
export const DARK_CLASS = 'dark';
/** `<html>` attribute carrying the user's explicit choice. Absent ⇒ no choice made. */
export const CHOICE_ATTR = 'data-theme-choice';
/** `<html>` attribute carrying the tenant's branding mode (always present). */
export const MODE_ATTR = 'data-theme-mode';
/** The client hint that carries the OS preference (request header, and the `Accept-CH` opt-in). */
export const COLOUR_SCHEME_HINT = 'Sec-CH-Prefers-Color-Scheme';
/** ~13 months, matching the session cookie: a display preference should outlive a login. */
export const THEME_COOKIE_MAX_AGE = 60 * 60 * 24 * 400;

/** A rendered colour scheme. */
export type ColourScheme = 'light' | 'dark';
/** What the user may choose. `system` = follow the OS. */
export type ThemeChoice = ColourScheme | 'system';
/** The tenant's branding default (`Branding['mode']`). `auto` = no opinion. */
export type TenantMode = 'auto' | 'light' | 'dark';

/** The three switcher states, in menu order. */
export const THEME_CHOICES: readonly ThemeChoice[] = ['light', 'dark', 'system'];

export interface ThemeInput {
	/** The user's explicit choice (parsed cookie / `data-theme-choice`), or null if unset. */
	choice?: ThemeChoice | null;
	/** The tenant's branding mode, or null if the portal has no branding (admin). */
	mode?: TenantMode | null;
	/** Whether the OS prefers dark. `null`/`undefined` = not knowable here (server, no hint). */
	prefersDark?: boolean | null;
}

/** A `ThemeChoice`, or null if the value is absent/unrecognised. */
export function parseThemeChoice(raw: string | null | undefined): ThemeChoice | null {
	return raw === 'light' || raw === 'dark' || raw === 'system' ? raw : null;
}

/** A `TenantMode`, or null if the value is absent/unrecognised. */
export function parseTenantMode(raw: string | null | undefined): TenantMode | null {
	return raw === 'auto' || raw === 'light' || raw === 'dark' ? raw : null;
}

/**
 * The stored choice out of a cookie header string — a request's `Cookie:` or `document.cookie`.
 * Returns null when the cookie is absent or holds anything but the three known values.
 */
export function themeChoiceFromCookie(header: string | null | undefined): ThemeChoice | null {
	if (!header) return null;
	for (const part of header.split(';')) {
		const eq = part.indexOf('=');
		if (eq < 0) continue;
		if (part.slice(0, eq).trim() !== THEME_COOKIE) continue;
		return parseThemeChoice(decodeURIComponent(part.slice(eq + 1).trim()));
	}
	return null;
}

/**
 * Which of the three states is actually in force — the user's choice if they made one, else the
 * tenant's mode, else `system`. This is what the toggle shows as selected: with no cookie and a firm
 * that has forced dark, "Dark" is the honest answer.
 */
export function effectiveChoice(input: ThemeInput): ThemeChoice {
	const choice = parseThemeChoice(input.choice ?? null);
	if (choice) return choice;
	const mode = parseTenantMode(input.mode ?? null);
	return mode === 'light' || mode === 'dark' ? mode : 'system';
}

/**
 * The scheme to render, or `null` when it depends on the OS preference and that is not known here
 * (server-side, before the client hint arrives). A null means "emit no class and let `themeHead()`'s
 * script decide" — never "light".
 */
export function resolveScheme(input: ThemeInput): ColourScheme | null {
	const effective = effectiveChoice(input);
	if (effective !== 'system') return effective;
	const prefersDark = input.prefersDark;
	if (prefersDark === null || prefersDark === undefined) return null;
	return prefersDark ? 'dark' : 'light';
}

/**
 * The OS preference out of a `Sec-CH-Prefers-Color-Scheme` request header, or null when the browser
 * has not sent one (or sent something unrecognised).
 */
export function prefersDarkFromHint(value: string | null | undefined): boolean | null {
	if (!value) return null;
	const v = value.trim().replace(/^"|"$/g, '').toLowerCase();
	return v === 'dark' ? true : v === 'light' ? false : null;
}

/**
 * The `Set-Cookie`-style string for a choice, for `document.cookie` in the browser. `secure` is
 * caller-supplied because only the page knows whether it is on https (localhost dev is not).
 */
export function themeCookie(
	choice: ThemeChoice,
	opts?: { secure?: boolean; maxAge?: number }
): string {
	const value = parseThemeChoice(choice) ?? 'system';
	const parts = [
		`${THEME_COOKIE}=${value}`,
		'path=/',
		`max-age=${opts?.maxAge ?? THEME_COOKIE_MAX_AGE}`,
		'samesite=lax'
	];
	if (opts?.secure) parts.push('secure');
	return parts.join('; ');
}

/**
 * The `<html>` attributes to render server-side (replaces `%theme.attrs%` in app.html): the `dark`
 * class when the scheme is known to be dark, plus the two attributes the script and the toggle read.
 * Every value comes from a closed enum, so there is nothing to escape.
 */
export function themeAttributes(input: ThemeInput): string {
	const attrs: string[] = [];
	// Light is the absence of the class, so only dark writes one. An unresolvable scheme writes
	// nothing and the blocking script fills it in.
	if (resolveScheme(input) === 'dark') attrs.push(`class="${DARK_CLASS}"`);
	const choice = parseThemeChoice(input.choice ?? null);
	if (choice) attrs.push(`${CHOICE_ATTR}="${choice}"`);
	attrs.push(`${MODE_ATTR}="${parseTenantMode(input.mode ?? null) ?? 'auto'}"`);
	return attrs.join(' ');
}

/**
 * The blocking `<head>` script (replaces `%theme.head%`, injected AFTER `%tenant.head%` so it
 * outranks the tenant-mode script `brandingHead` emits). It re-derives the scheme from the `<html>`
 * attributes — the same precedence as `resolveScheme` — and keeps it in step if the OS preference
 * changes while the page is open.
 *
 * It re-reads the attributes on every call rather than closing over the value, so when the toggle
 * updates `data-theme-choice` at runtime this listener agrees with it instead of fighting it.
 */
export function themeHead(): string {
	const script = [
		'(function(){try{',
		'var r=document.documentElement,q=window.matchMedia("(prefers-color-scheme: dark)"),',
		`read=function(){var c=r.getAttribute("${CHOICE_ATTR}"),m=r.getAttribute("${MODE_ATTR}");`,
		'if(c==="light"||c==="dark"||c==="system")return c;',
		'return (m==="light"||m==="dark")?m:"system";},',
		`apply=function(){var e=read();r.classList.toggle("${DARK_CLASS}",e==="dark"||(e==="system"&&q.matches));};`,
		'apply();q.addEventListener("change",apply);',
		'}catch(e){}})();'
	].join('');
	return `<script>${script}</script>`;
}
