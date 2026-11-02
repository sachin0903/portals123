/**
 * WHICH national identifier a client must give, what it is called, and how a nationality is
 * labelled. ONE implementation — `field-shell.ts`' arrangement, for the same reason.
 *
 * The rules are the regulator's, not ours. MiFIR RTS 22 / the FCA's onshored transaction-reporting
 * regime says a natural person is identified by their NATIONALITY, not by where they live or where
 * the firm is: an identifier is chosen per country in a stated order of preference, and every
 * country's list ends in CONCAT — a derived fallback for people with no national number to give.
 * `ref.countries.identifiers` (0050) holds that list per country as
 * `{name, regex, example, priority, localeName}`.
 *
 * THE TYPE IS READ FROM THE DATA, NEVER HARDCODED. "NINO" is not the name of anything: the United
 * Kingdom's priority-0 entry is **"National Insurance Number"**, which is what a form should ask
 * for and what must be stored alongside the value — a firm reading a record back needs to know
 * which scheme the digits belong to, and a report needs the regulator's own word for it.
 *
 * ## Why it is here and not in either surface
 *
 * It existed TWICE — `lib/forms/national-identifier.ts` for the dynamic form engine (the
 * onboarding fact-find) and `lib/components/book/clients/national-identifier.ts` for the client
 * record — and the two had already diverged on the case that matters. The older one padded the
 * name BEFORE testing it for emptiness, so `pad5('')` is `'#####'` and it returned a CONCAT for a
 * client with no surname: a partial CONCAT is not a shorter identifier, it is a wrong one, and the
 * whole point of the format is that two firms holding the same person derive the same string. The
 * newer one returned null and its own test pinned that. This module keeps the newer behaviour.
 *
 * Neither surface owns the rule — the identifier REGIME is derived from the nationality set, so
 * two surfaces answering differently can put one client under different identifier rules, which
 * is a reporting error rather than a cosmetic one.
 *
 * Two shapes reach it: the API's `ref.countries` rows say `in_eea`, and the form engine's own
 * static reference rows say `inEea`. Both are accepted and normalised here, in one place, rather
 * than kept apart by two types — which is what let the two implementations exist at all.
 */

export type CountryIdentifierSpec = {
	name: string;
	regex: string[];
	example?: string;
	priority: number;
	localeName?: string;
};

export type IdentifierCountry = {
	code: string;
	name?: string | null;
	adjective?: string | null;
	/** The API's spelling (`ref.countries.in_eea`). */
	in_eea?: boolean | null;
	/** The form engine's static reference rows' spelling. */
	inEea?: boolean | null;
	identifiers?: unknown;
};

/** The UK is treated as EEA for this purpose. The regulation's preference order predates the
 *  withdrawal and the FCA's onshored regime kept it, so a British nationality still wins over a
 *  non-EEA one — dropping GB here would quietly re-rank every dual national. */
function isEeaLike(country: IdentifierCountry | undefined): boolean {
	if (!country) return false;
	const code = String(country.code ?? '').toUpperCase();
	if (code === 'GB' || code === 'UK') return true;
	return country.in_eea === true || country.inEea === true;
}

/** `identifiers`, parsed and sorted by priority. Tolerant on purpose: the column is jsonb written
 *  by a seed, and a malformed row should cost that one entry rather than the whole list. */
export function parseIdentifiers(value: unknown): CountryIdentifierSpec[] {
	if (!Array.isArray(value)) return [];
	return value
		.map((row): CountryIdentifierSpec | null => {
			if (!row || typeof row !== 'object') return null;
			const obj = row as Record<string, unknown>;
			const name = typeof obj.name === 'string' ? obj.name : '';
			if (!name) return null;
			return {
				name,
				regex: Array.isArray(obj.regex)
					? obj.regex.filter((r): r is string => typeof r === 'string')
					: [],
				priority: typeof obj.priority === 'number' ? obj.priority : Number.MAX_SAFE_INTEGER,
				...(typeof obj.example === 'string' ? { example: obj.example } : {}),
				...(typeof obj.localeName === 'string' ? { localeName: obj.localeName } : {})
			};
		})
		.filter((v): v is CountryIdentifierSpec => v !== null)
		.sort((a, b) => a.priority - b.priority);
}

/**
 * Which of a client's nationalities decides the identifier.
 *
 * An EEA nationality wins over a non-EEA one; among several EEA nationalities the first by ISO
 * code. Alphabetical is arbitrary and that is the point — it is DETERMINISTIC, so two firms
 * reporting the same dual national reach the same identifier, which is the whole reason the
 * regulation states a rule rather than leaving it to judgement.
 *
 * Non-EEA only: the reference data's "all other countries" bucket if it carries one (`ZZ`/`XX`),
 * else the first nationality given.
 */
export function pickIdentifierCountry(
	nationalityCodes: readonly string[],
	byCode: Map<string, IdentifierCountry>
): string | null {
	const codes = nationalityCodes
		.map((c) =>
			String(c ?? '')
				.trim()
				.toUpperCase()
		)
		.filter(Boolean);
	if (codes.length === 0) return null;

	const eea = codes.filter((code) => isEeaLike(byCode.get(code)));
	if (eea.length > 0) return [...eea].sort((a, b) => a.localeCompare(b))[0] ?? null;

	if (byCode.has('ZZ')) return 'ZZ';
	if (byCode.has('XX')) return 'XX';
	return codes[0] ?? null;
}

/** Every identifier a country asks for, in its stated order of preference. */
export function pickAllIdentifierSpecs(
	countryCode: string | null,
	byCode: Map<string, IdentifierCountry>
): CountryIdentifierSpec[] {
	if (!countryCode) return [];
	return parseIdentifiers(byCode.get(countryCode.toUpperCase())?.identifiers);
}

/** The identifier a country asks for first. */
export function pickIdentifierSpec(
	countryCode: string | null,
	byCode: Map<string, IdentifierCountry>
): CountryIdentifierSpec | null {
	return pickAllIdentifierSpecs(countryCode, byCode)[0] ?? null;
}

/**
 * CONCAT, the derived identifier: `YYYYMMDD` + five characters of the first name + five of the
 * surname, upper-cased, accents stripped, punctuation and spaces removed, each name padded with
 * `#` to exactly five.
 *
 * It is COMPUTED, never typed. The whole point of the format is that two firms holding the same
 * person derive the same string — a hand-typed one that pads with spaces, keeps an apostrophe or
 * truncates at four defeats the identifier's only purpose. `null` when the date of birth or either
 * name is missing, because a partial CONCAT is not a shorter identifier, it is a wrong one — the
 * older of the two implementations padded first and so answered `'#####'` for a missing name.
 *
 * `dob` takes a `Date` as well as an ISO string because the form engine's `values` map holds
 * whichever the field produced.
 */
export function computeConcat(params: {
	dob?: string | Date | null;
	firstName?: string | null;
	surname?: string | null;
}): string | null {
	const raw = params.dob;
	const at =
		raw instanceof Date
			? raw
			: typeof raw === 'string' && /^\d{4}-\d{2}-\d{2}/.test(raw)
				? new Date(`${raw.slice(0, 10)}T00:00:00.000Z`)
				: null;
	if (!at || Number.isNaN(at.getTime())) return null;

	const clean = (v: string | null | undefined): string =>
		String(v ?? '')
			.toUpperCase()
			.replace(/['`\-.,\s]/g, '')
			// NFD first, then strip the combining marks it separates out: É becomes E rather than
			// being dropped, which is what a plain [^A-Z] filter would do to half of Europe.
			.normalize('NFD')
			.replace(/[̀-ͯ]/g, '');
	const pad5 = (v: string) => (v + '#####').slice(0, 5);

	const first = clean(params.firstName);
	const last = clean(params.surname);
	if (!first || !last) return null;

	const yyyy = String(at.getUTCFullYear()).padStart(4, '0');
	const mm = String(at.getUTCMonth() + 1).padStart(2, '0');
	const dd = String(at.getUTCDate()).padStart(2, '0');
	return `${yyyy}${mm}${dd}${pad5(first)}${pad5(last)}`;
}

/**
 * Nationality options, labelled by ADJECTIVE — "British", not "United Kingdom".
 *
 * A nationality is a property of the person and reads as an adjective in every document a firm
 * produces; the country name is the answer to a different question (tax residency, country of
 * birth), which is why those two keep the name. Where an adjective is shared by more than one
 * country it is disambiguated with the country in brackets, because "Chinese" alone cannot be
 * posted as a code.
 *
 * IN SOURCE ORDER, and a country with no adjective falls back to its name rather than being
 * dropped. Both are corrections to the client-record copy of this rule, which sorted by label and
 * dropped it — so a `ref.countries` row with a blank adjective was answerable in the onboarding
 * fact-find (`resolveOptions` falls back to `adjective ?? name ?? value`) and unrepresentable on
 * the client record. Since the identifier REGIME is derived from the nationality, an unpickable
 * country is not a cosmetic gap. All 250 seeded rows carry an adjective, so nothing changes on
 * today's data; the ordering stays a per-surface choice made at the call site.
 */
export function nationalityOptions(
	countries: readonly IdentifierCountry[]
): { value: string; label: string }[] {
	const shared = new Map<string, number>();
	for (const c of countries) {
		const adjective = (c.adjective ?? '').trim();
		if (adjective) shared.set(adjective, (shared.get(adjective) ?? 0) + 1);
	}
	return countries
		.map((c) => {
			const code = (c.code ?? '').trim().toUpperCase();
			if (!code) return null;
			const adjective = (c.adjective ?? '').trim();
			const name = (c.name ?? '').trim();
			const ambiguous = !!adjective && (shared.get(adjective) ?? 0) > 1 && !!name;
			const label = ambiguous ? `${adjective} (${name})` : adjective || name || code;
			return { value: code, label };
		})
		.filter((o): o is { value: string; label: string } => o !== null);
}
