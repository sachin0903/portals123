/**
 * Identity for a client's onboarding step in a keyed `{#each}`.
 *
 * `step_key` is unique only WITHIN a case. `/applications/me` deliberately flattens the client's
 * steps across every case they are a party to — their profile/KYC case plus one per account — so the
 * same key (`terms_general`, say) legitimately arrives more than once, and the portal presents the
 * lot as a single checklist.
 *
 * Svelte 5 THROWS `each_key_duplicate` on a repeated key and aborts the surrounding subtree
 * mid-hydration. The page therefore SSR'd correctly, rendered for one frame, then blanked — every
 * `{#if}`/`{#each}` body cleared with nothing refilling it, no console error and nothing reaching
 * `handleError`, because the throw surfaced only as an unhandled rejection inside an effect.
 *
 * The backing request item is the real identity of a step instance, so prefer it. Staff-only steps
 * carry no item, so fall back to the key plus its position, which is unique by construction.
 */
export function stepKey(
	step: { item_uid?: string | null; step_key: string },
	index: number
): string {
	// Both branches are namespaced so a positional fallback can never collide with a real item uid —
	// `{step_key: 'a', item_uid: null}` at index 1 would otherwise produce the same `a:1` as a step
	// whose item uid happens to be `a:1`, which is the very bug this function exists to prevent.
	return step.item_uid ? `item:${step.item_uid}` : `pos:${index}:${step.step_key}`;
}

/** One row of `GET /terms/outstanding` — a published term this client has still to accept. */
export type OutstandingTerm = {
	uid: string;
	title: string;
	owner: string;
	body_html?: string | null;
	has_file?: boolean;
	file_content_type?: string | null;
};

/**
 * The outstanding catalogue terms the page lists as steps of their own.
 *
 * The account cannot be opened until every owner has accepted each of the firm's current
 * onboarding terms. The seeded journey's declarations pin library documents, not catalogue
 * terms, so a term the firm authored afterwards has no step that collects it — and this list is
 * the only place the client can accept it. It used to keep Graphene's terms alone and drop any
 * term whose title matched a step's, which hid exactly those: the firm's "Platform Terms"
 * vanished behind a "Platform terms" step that pinned a different document, and the adviser
 * reached account setup with a refusal nobody could clear.
 *
 * A term a step DOES pin is left out, by the pinned uid: that step is where it is accepted, and
 * once it is the term stops being outstanding anyway.
 */
export function catalogueTerms<T extends OutstandingTerm>(
	outstanding: readonly T[],
	steps: readonly { term_uid?: string | null }[]
): T[] {
	const pinned = new Set(steps.map((s) => s.term_uid).filter((uid): uid is string => !!uid));
	return outstanding.filter((item) => !pinned.has(item.uid));
}

/** Definition order for client terms, including already-agreed ones.
 *
 * `/applications/me` sorts outstanding work first, so a pending declaration is always index 0
 * in display order. The "Document 1 of X" counter has to follow the journey's authored sequence
 * or agreeing the first one leaves the dialog saying 1 of a shrinking remainder.
 */
export function sortTermsByDefinitionOrder<T extends { key: string; order?: number | null }>(
	terms: T[]
): T[] {
	return [...terms].sort((a, b) => {
		const ao = a.order ?? Number.MAX_SAFE_INTEGER;
		const bo = b.order ?? Number.MAX_SAFE_INTEGER;
		if (ao !== bo) return ao - bo;
		return a.key.localeCompare(b.key);
	});
}

export function termsAgreeIndex(
	terms: { key: string; order?: number | null }[],
	openKey: string | null | undefined
): number {
	if (!openKey) return 0;
	return sortTermsByDefinitionOrder(terms).findIndex((step) => step.key === openKey) + 1;
}

/** The roles an account can name, in reading order, with the words the portal uses for them. */
export const PARTY_ROLES: { role: string; one: string; many: string }[] = [
	{ role: 'owner', one: 'Owner', many: 'Owners' },
	{ role: 'registered_contact', one: 'Registered contact', many: 'Registered contacts' },
	{ role: 'decision_maker', one: 'Decision maker', many: 'Decision makers' },
	{ role: 'trustee', one: 'Trustee', many: 'Trustees' },
	{ role: 'attorney', one: 'Attorney', many: 'Attorneys' },
	{ role: 'beneficiary', one: 'Beneficiary', many: 'Beneficiaries' },
	{ role: 'other', one: 'Also named', many: 'Also named' }
];

function humaniseRole(role: string): string {
	return (role ?? '')
		.trim()
		.replace(/[_-]+/g, ' ')
		.replace(/^\w/, (m) => m.toUpperCase());
}

/** One role in the portal's own words, singular. */
export function roleLabel(role: string): string {
	const known = PARTY_ROLES.find((r) => r.role === (role ?? '').trim().toLowerCase());
	return known ? known.one : humaniseRole(role) || 'Named on the application';
}

export type ReviewParty = { name: string; role: string };

/**
 * Who is named on an account, grouped under the role they hold.
 *
 * The dossier used to show one "Owners" row over EVERY party, which on a JISA named the child
 * and the adult who acts for them as owners — and the adult is who reads this page. A JISA's
 * whole structure is that the child owns the money and cannot reach it until they are 18, so
 * telling the parent they own it is wrong about the one thing the product turns on.
 *
 * `parties` is optional on the dossier, so the flat owner list is the fallback: that is what the
 * page showed before, and an older API build must not lose the row entirely.
 */
export function partyRows(account: {
	owners?: string[];
	parties?: ReviewParty[];
}): { label: string; value: string }[] {
	const parties = (account.parties ?? []).filter((p) => (p.name ?? '').trim());
	if (!parties.length) {
		const owners = (account.owners ?? []).map((n) => n.trim()).filter(Boolean);
		if (!owners.length) return [];
		return [{ label: owners.length > 1 ? 'Owners' : 'Owner', value: owners.join(', ') }];
	}
	const rows: { label: string; value: string }[] = [];
	const known = new Set(PARTY_ROLES.map((r) => r.role));
	for (const { role, one, many } of PARTY_ROLES) {
		const names = parties.filter((p) => p.role === role).map((p) => p.name.trim());
		if (names.length) rows.push({ label: names.length > 1 ? many : one, value: names.join(', ') });
	}
	// A role the portal has no wording for is still somebody on the account, so it keeps its own
	// humanised label rather than being dropped.
	for (const party of parties.filter((p) => !known.has(p.role))) {
		rows.push({ label: roleLabel(party.role), value: party.name.trim() });
	}
	return rows;
}
