/**
 * The flag that sits beside an ISO 4217 code in a currency pill (`CurrencyBadge`).
 *
 * ISO 4217 is built on ISO 3166: the first two letters of a currency code are the alpha-2
 * country code (`GBP` → `GB`, `CHF` → `CH`, `EUR` → the `EU` reserved code), so the country
 * needs no table of its own. The `X` codes are the deliberate exception — supranational and
 * commodity codes (`XCD`, `XOF`, `XAU`, `XDR`) belong to no country and get no flag; the pill
 * keeps a neutral disc in that position so a column of codes still lines up.
 *
 * The artwork is HatScripts' `circle-flags` (MIT): one circular SVG per territory, already
 * clipped, so the pill needs no mask of its own. They are pulled in as hashed asset URLs,
 * `no-inline` on purpose — at ~1 KB each every flag is under Vite's inline threshold, and
 * inlining 265 of them as data URIs would put ~300 KB into the chunk of any page that shows a
 * single pill. As files, a flag is one small cached request the first time it is seen. Only
 * the two-letter territory files are globbed; the pack also carries sub-national and
 * alternative designs (`gb-eng`, `us-ca`, `au-aboriginal`) that no currency maps to.
 */
const FLAG_FILES = import.meta.glob('../../node_modules/circle-flags/flags/[a-z][a-z].svg', {
	eager: true,
	import: 'default',
	query: '?no-inline'
}) as Record<string, string>;

/** Hashed asset URL per lowercase ISO 3166 alpha-2 code. */
export const FLAGS_BY_COUNTRY: Readonly<Record<string, string>> = Object.freeze(
	Object.fromEntries(
		Object.entries(FLAG_FILES).map(([path, url]) => [path.slice(-6, -4), url] as const)
	)
);

/** Lowercase alpha-2 territory for an ISO 4217 code, or `undefined` for an `X` code or garbage. */
export function currencyCountry(code: string | null | undefined): string | undefined {
	const iso = (code ?? '').trim().toUpperCase();
	if (!/^[A-Z]{3}$/.test(iso) || iso.startsWith('X')) return undefined;
	return iso.slice(0, 2).toLowerCase();
}

/** The flag URL for an ISO 4217 code, or `undefined` when there is no country to show. */
export function currencyFlag(code: string | null | undefined): string | undefined {
	const country = currencyCountry(code);
	return country ? FLAGS_BY_COUNTRY[country] : undefined;
}
