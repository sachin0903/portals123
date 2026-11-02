/**
 * ONE namer for a code the UI has to print as words.
 *
 * The product's rule is SENTENCE CASE (`docs/design-language.md`), and the way it was being broken
 * was never a heading — it was a VALUE. A status, role or kind arriving from the API is a
 * `snake_case` key, and three habits had grown up around printing one:
 *
 *   1. render it raw — a lowercase "draft" or "blocked" badge sitting beside a `roleLabel`'d
 *      "Owner" in the same row;
 *   2. `class="capitalize"` — which is TITLE case (CSS uppercases every word, so
 *      `not_a_contribution` reads "Not_a_contribution" and "bank transfer" reads "Bank Transfer");
 *   3. a per-surface label map, which is right when the words differ from the key and drift when
 *      they do not.
 *
 * So this is the fallback for (1) and (2), and the tail of (3). It is deliberately NOT a
 * title-caser: there is no correct general title-casing of an enum, and sentence case is what the
 * rest of the chrome uses.
 *
 * Separators are `_ - .` because all three are real key shapes here (`execution_only`,
 * `morgan-lloyd`, `document.sent`). Only the FIRST letter is touched — the rest of the string is
 * left exactly as it came, so an acronym survives (`GIA` stays `GIA`, never `Gia`).
 */
export function sentenceCase(value: string): string {
	const spaced = value.replace(/[._-]+/g, ' ').trim();
	// `charAt`, not `[0]`: indexed access on a string is `string | undefined` under the package's
	// strict settings, and the guard above does not narrow it.
	return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : value;
}

/**
 * `sentenceCase` with the book's own answer for "nobody said": an em dash.
 *
 * The two fallbacks are separate on purpose — a listing cell with no value shows the dash every
 * other empty cell shows, where a template code that resolves to nothing must not be printed as
 * a dash in the middle of a sentence.
 */
export function humanise(value: string | null | undefined): string {
	return value ? sentenceCase(value) : '—';
}
