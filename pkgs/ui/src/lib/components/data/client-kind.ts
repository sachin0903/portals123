/**
 * What KIND of client a contact is, for the glyph that stands for them.
 *
 * `crm.contacts.type` only knows `individual` and `organisation`. A CHILD is an individual under
 * 18 by date of birth, which is the rule the API applies at kickoff (`catalogue.completed_age`)
 * and the one the JISA product enforces. Decided here once so the journey, the client record and
 * the client list mark the same person the same way.
 */

import { formatDate } from '../../dates.js';

export type ClientKind = 'individual' | 'organisation' | 'group' | 'child';

/** What the disc may stand for: a kind of client, or an ACCOUNT. The account is not a
 *  `ClientKind` — `clientKind()` decides who a person is and must never answer "account" — but
 *  the account record wears the same disc, and a disc with a person on it above "Oakden SIPP"
 *  said the record was about the owner. */
export type AvatarKind = ClientKind | 'account';

const ADULT_AGE = 18;

/** Years of age completed on `on` (today when omitted), or null when the date is unusable. */
export function completedAge(dob: string | null | undefined, on: Date = new Date()): number | null {
	if (!dob) return null;
	const born = new Date(dob);
	if (Number.isNaN(born.getTime())) return null;
	// A bare `YYYY-MM-DD` is UTC midnight, so read its parts in UTC.
	const utc = /^\d{4}-\d{2}-\d{2}$/.test(dob.trim());
	const year = utc ? born.getUTCFullYear() : born.getFullYear();
	const month = utc ? born.getUTCMonth() : born.getMonth();
	const day = utc ? born.getUTCDate() : born.getDate();
	let years = on.getFullYear() - year;
	const beforeBirthday = on.getMonth() < month || (on.getMonth() === month && on.getDate() < day);
	if (beforeBirthday) years -= 1;
	return years;
}

/** Whether this date of birth makes the person a child. Unknown date: not a child. */
export function isChild(dob: string | null | undefined, on: Date = new Date()): boolean {
	const age = completedAge(dob, on);
	return age !== null && age < ADULT_AGE;
}

/** The glyph a client wears: an organisation, a child, or an individual. */
export function clientKind(
	client: { type?: string | null; dob?: string | null },
	on: Date = new Date()
): ClientKind {
	if (client.type === 'organisation') return 'organisation';
	if (isChild(client.dob, on)) return 'child';
	return 'individual';
}

/** "Born 3 Mar 2014 (12)" — the date with the age, or null with no date. */
export function birthLine(dob: string | null | undefined, on: Date = new Date()): string | null {
	const age = completedAge(dob, on);
	if (age === null || !dob) return null;
	return `Born ${formatDate(dob)} (${age})`;
}
