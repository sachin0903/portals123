export function completedAge(dob: string, on: Date = new Date()): number | null {
	const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(dob);
	if (!match) return null;
	const born = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
	let years = on.getFullYear() - born.getFullYear();
	const month = on.getMonth() - born.getMonth();
	if (month < 0 || (month === 0 && on.getDate() < born.getDate())) years -= 1;
	return years;
}

export type Custody = 'onshore' | 'offshore';

export type CustodyPlatform = {
	id: string;
	openable_custodies?: Custody[];
};

/** Where this custodian can actually open. Seccl is onshore only even if the
 *  catalogue row is stale; SEI follows the attached `custody_orgs` map. */
export function openableCustodies(
	provider: string | null | undefined,
	platform: CustodyPlatform | undefined
): Custody[] {
	if (provider === 'seccl') return ['onshore'];
	const listed = platform?.openable_custodies?.filter(
		(custody): custody is Custody => custody === 'onshore' || custody === 'offshore'
	);
	if (listed?.length) return listed;
	return ['onshore', 'offshore'];
}

export function custodiesFor(
	allowed: Custody[] | undefined,
	provider: string | null | undefined,
	platforms: CustodyPlatform[] | null | undefined
): Custody[] {
	const product = new Set(allowed?.length ? allowed : (['onshore', 'offshore'] as Custody[]));
	const platform = (platforms ?? []).find((row) => row.id === provider);
	return openableCustodies(provider, platform).filter((custody) => product.has(custody));
}

export function defaultCustody(custodies: Custody[]): Custody | null {
	if (custodies.includes('onshore')) return 'onshore';
	return custodies[0] ?? null;
}

/**
 * Keep a just-generated illustration in the kickoff list.
 *
 * The wizard selects it, then reloads the catalogue. If that reload has not listed the new
 * row yet, the selection is set on an account whose card is not on screen, and the adviser
 * has to find it. A row the reload already returned is left as the server sent it.
 */
export function keepGeneratedIllustration<T extends { uid: string }>(
	loaded: T[],
	generated: T
): T[] {
	if (loaded.some((row) => row.uid === generated.uid)) return loaded;
	return [generated, ...loaded];
}

/**
 * Whether the signed-in person is one of the firm's advisers.
 *
 * The portal knows its user by WorkOS id and email; the members list carries the email, so
 * that is the join. `advisers` is the members list already cut to `is_adviser`.
 */
export function signedInIsAdviser(
	email: string | null | undefined,
	advisers: readonly { email: string | null }[]
): boolean {
	const mine = (email ?? '').trim().toLowerCase();
	if (!mine) return false;
	return advisers.some((m) => (m.email ?? '').trim().toLowerCase() === mine);
}

/** What the adviser rule needs to know about one applicant card. */
export type AdviserParty = {
	mode: 'new' | 'existing';
	/** The existing client's own adviser; null for a new client or one with nobody. */
	adviser_id: string | null;
};

/**
 * Whether the kickoff wizard must have an adviser chosen before it can start.
 *
 * Left blank, the API gives the journey to the first applicant who already has an adviser, and
 * only then to the signed-in user when they are marked an adviser. A new client has nobody to
 * inherit from, so an administrator starting one used to send a journey the API refuses — or,
 * before it refused, recorded a non-adviser and failed with a 500. The picker is required in
 * exactly that case: nobody on the application brings an adviser, and the person signed in is
 * not one.
 */
export function adviserChoiceRequired(
	parties: readonly AdviserParty[],
	signedInIsAdviser: boolean
): boolean {
	if (signedInIsAdviser) return false;
	return !parties.some((party) => party.mode === 'existing' && !!party.adviser_id);
}

/**
 * The live journey a kickoff was refused for.
 *
 * The API's 409 carries `{message, execution_uid}`; the uid is absent when the database's
 * unique index caught a race the pre-check did not see, so the wizard can only offer to open
 * or stop a journey it can name.
 */
export function liveJourneyUid(body: unknown): string | null {
	const uid = (body as { detail?: { execution_uid?: unknown } } | null)?.detail?.execution_uid;
	return typeof uid === 'string' && uid ? uid : null;
}
