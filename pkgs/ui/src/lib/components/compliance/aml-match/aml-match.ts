/**
 * An AML screening match as Graphene shows it: who ComplyCube matched, how strongly, on which
 * lists, and the profile behind the match (photos, names, roles, ties, articles). The AML console
 * shows it to the operator deciding the match; a firm's compliance tab shows a match once
 * Graphene confirmed it and let the onboarding continue past it, never with the decision.
 */
import { countryName } from '../../../countries.js';
import { humanise } from '../../../sentence-case.js';

export type MatchAddress = {
	line: string | null;
	city: string | null;
	postal_code: string | null;
	country: string | null;
};

/** A list a match was found on. `url` only when ComplyCube sent one. */
export type MatchReference = {
	source: string | null;
	date: string | null;
	to_date: string | null;
	url: string | null;
};

/** One of a match's names. `type` is ComplyCube's, as words: "Primary", "Alias", "Localised name". */
export type MatchName = { name: string; type: string | null };

/** A position the matched person holds or held. Dates are partial ISO: "2014", "2014-11",
 *  "2014-11-19". */
export type MatchRole = {
	title: string;
	category: string | null;
	/** ComplyCube's kind of role, as words: "Primary Occupation", "Previous Roles". */
	type: string | null;
	from: string | null;
	to: string | null;
	current: boolean;
};

/** A country ComplyCube ties the match to, and why: "Born in", "Citizenship", "Resident of". */
export type MatchCountry = { country: string; type: string | null };

/** A dated fact on the match: "Date of Birth", "Inactive as of (PEP)". The date is partial ISO. */
export type MatchDate = { type: string; date: string };

/** A relative or associate of the matched person. */
export type MatchAssociation = {
	name: string;
	relationship: string | null;
	current: boolean | null;
	categories: string[];
	entity_type: string | null;
	/** A photo on a third-party site, or null. */
	image: string | null;
};

/** Who ComplyCube matched on an AML screen, as the console and the compliance tab show them:
 *  the facts, the lists, and the profile behind them. Never Graphene's decision on it. */
export type AmlMatchProfileData = {
	name: string;
	match_types: string[];
	/** The match's categories, already words ("PEP level 1", "Sanctions lists"). */
	categories: string[];
	score: number | null;
	dob: string | null;
	gender: string | null;
	countries: string[];
	addresses: MatchAddress[];
	sources: string[];
	references: MatchReference[];
	/** Photos of the matched person, on third-party sites. */
	images: string[];
	/** Every name, the primary one included. */
	names: MatchName[];
	/** The names other than the primary one. */
	aliases: string[];
	/** Current first, then newest. */
	roles: MatchRole[];
	birth_places: string[];
	country_details: MatchCountry[];
	dates: MatchDate[];
	/** "person", "organisation". */
	entity_type: string | null;
	/** ComplyCube's own account of the match. */
	notes: string | null;
	associations: MatchAssociation[];
	/** The articles behind the match, on third-party sites. */
	source_links: string[];
	/** "Politically Exposed Person (PEP)", "Special Interest Person (SIP)". */
	types: string[];
	active: boolean | null;
	deceased: boolean | null;
	/** As ComplyCube gives it: "14-May-2026". */
	last_reviewed: string | null;
};

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** A date ComplyCube may give only in part — `1958-05-26`, `1958-05` or `1958` — as far as it
 *  goes: "26 May 1958", "May 1958", "1958". Anything else is shown as it came. */
export function partialDate(value: string | null): string {
	if (!value) return '—';
	const found = /^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?$/.exec(value.trim());
	if (!found) return value;
	const [, year, month, day] = found;
	const name = month ? MONTHS[Number(month) - 1] : undefined;
	if (month && !name) return value;
	return [day ? String(Number(day)) : null, name ?? null, year].filter(Boolean).join(' ');
}

/** A match's strength as the card prints it: "93%". */
export function matchScore(score: number | null): string | null {
	return score === null || Number.isNaN(score) ? null : `${Math.round(score)}%`;
}

/** The line under a match's name: what is known of its gender and countries. */
export function matchSubtitle(match: Pick<AmlMatchProfileData, 'gender' | 'countries'>): string {
	return [match.gender ? humanise(match.gender) : null, ...match.countries.map(countryName)]
		.filter(Boolean)
		.join(' · ');
}

export function addressLine(address: MatchAddress): string {
	return [address.line, address.city, address.postal_code, countryName(address.country)]
		.filter(Boolean)
		.join(', ');
}

/** A link's site, for a list of links: "https://www.bbc.co.uk/news/…" → "bbc.co.uk". The link as
 *  it came when it cannot be read. */
export function linkHost(url: string): string {
	try {
		return new URL(url).hostname.replace(/^www\./, '') || url;
	} catch {
		return url;
	}
}

/** When a role was held: "Nov 2014 – May 2023", "Nov 2014 – now", "From 2014", "Until 2016",
 *  "Current". Empty when ComplyCube gave no dates and the role is not current. */
export function roleDates(role: Pick<MatchRole, 'from' | 'to' | 'current'>): string {
	const from = role.from ? partialDate(role.from) : null;
	const to = role.to ? partialDate(role.to) : null;
	if (from && to) return `${from} – ${to}`;
	if (from) return role.current ? `${from} – now` : `From ${from}`;
	if (to) return `Until ${to}`;
	return role.current ? 'Current' : '';
}

/** Where and when the match was born: "19 Jul 1970 · Irvine, United Kingdom". Empty when neither is
 *  known. More than one place reads as alternatives: "Irvine; Glasgow". */
export function bornLine(match: Pick<AmlMatchProfileData, 'dob' | 'birth_places'>): string {
	const places = match.birth_places.filter((place) => place.trim()).join('; ');
	return [match.dob ? partialDate(match.dob) : null, places || null].filter(Boolean).join(' · ');
}

/** Each country once, with every reason ComplyCube gave for it: "United Kingdom" — "Born in",
 *  "Citizenship". A code and its name are the same country. In the order ComplyCube sent them. */
export function countryReasons(details: MatchCountry[]): { country: string; reasons: string[] }[] {
	const grouped = new Map<string, string[]>();
	for (const { country, type } of details) {
		const name = countryName(country.trim());
		if (!name) continue;
		const reasons = grouped.get(name) ?? [];
		if (type?.trim() && !reasons.includes(type.trim())) reasons.push(type.trim());
		grouped.set(name, reasons);
	}
	return [...grouped].map(([country, reasons]) => ({ country, reasons }));
}

/** Items under their `type`, in the order each type first appears. No type reads as `fallback`. */
export function groupByType<T extends { type: string | null }>(
	items: T[],
	fallback = 'Other'
): { type: string; items: T[] }[] {
	const grouped = new Map<string, T[]>();
	for (const item of items) {
		const type = item.type?.trim() || fallback;
		grouped.set(type, [...(grouped.get(type) ?? []), item]);
	}
	return [...grouped].map(([type, grouped]) => ({ type, items: grouped }));
}
