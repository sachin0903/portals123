/**
 * Initials for a name — was eleven near-identical implementations across `pkgs/ui` and
 * `apps/client`, each with its own fallback for an empty/missing name (a blank string, `'?'`, a
 * brand letter, an en dash). One shared pair, `initials`/`personInitials`, with the fallback an
 * explicit argument so every call site keeps whatever it already rendered.
 *
 * Deliberately NOT the instrument-name rule (`components/investment/instrument-icon.ts`'s
 * `instrumentInitials`), which must never drop a real word — a fund called "Dr Martens" keeps DM,
 * not just M.
 */

/**
 * Up to two initials from a name. Two letters is the most that stays legible at avatar tile sizes.
 */
export function initials(name: string, fallback = '?'): string {
	return (
		name
			.split(/\s+/)
			.filter(Boolean)
			.slice(0, 2)
			.map((word) => word[0])
			.join('')
			.toUpperCase() || fallback
	);
}

/** Titles that are not part of a person's name. Matched case-insensitively, with or without a dot. */
const HONORIFICS = new Set([
	'mr',
	'mrs',
	'ms',
	'miss',
	'mx',
	'dr',
	'prof',
	'professor',
	'sir',
	'dame',
	'lord',
	'lady',
	'rev',
	'revd',
	'reverend'
]);

/**
 * Initials for a PERSON, ignoring a leading honorific: "Mr Max Oakden" ⇒ MO, not MM.
 *
 * Clients and advisers are stored and displayed with their title, so the naive version disagrees
 * with the avatar on the record's own page (MO) — two different monograms for one person on one
 * screen. Separate from `initials` on purpose: a brand lockup must NOT strip words.
 */
export function personInitials(name: string, fallback = '?'): string {
	const words = name.split(/\s+/).filter(Boolean);
	const [first = ''] = words;
	const stripped = HONORIFICS.has(first.replace(/\.$/, '').toLowerCase()) ? words.slice(1) : words;
	// If the honorific was the whole of it, fall back to the raw name rather than to nothing.
	return initials((stripped.length ? stripped : words).join(' '), fallback);
}
