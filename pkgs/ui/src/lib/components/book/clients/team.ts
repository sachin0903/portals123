/**
 * A client's TEAM — `crm.client_advisers`, one row per person per client, each with a role.
 *
 * Four roles, a closed list (the API answers 422 to anything else). A client has at most ONE
 * `adviser`, and any number of the other three. Assigning a new adviser does not remove the old
 * one: they stay on the team as `support`. Re-assigning someone already on the team changes their
 * role, because there is one row per person per client.
 *
 * The list and its words live HERE and nowhere else. The record's Team dialog, the platform's form
 * action, the message rail, the AML header and the automation builder all read it, so a fifth role
 * is one edit in this file (and the API's own list).
 */
import { sentenceCase } from '../../../sentence-case.js';
import type { ClientAdviserRow, FirmMemberOption } from './types.js';

export const CLIENT_TEAM_ROLES = ['adviser', 'paraplanner', 'administrator', 'support'] as const;
export type ClientTeamRole = (typeof CLIENT_TEAM_ROLES)[number];

/** What a form that names no role means: the API's own default. */
export const DEFAULT_CLIENT_TEAM_ROLE: ClientTeamRole = 'adviser';

export const CLIENT_TEAM_ROLE_LABELS: Record<ClientTeamRole, string> = {
	adviser: 'Adviser',
	paraplanner: 'Paraplanner',
	administrator: 'Administrator',
	support: 'Support'
};

/** The role picker's options, in the list's order (Adviser first). */
export const CLIENT_TEAM_ROLE_ITEMS: { value: ClientTeamRole; label: string }[] =
	CLIENT_TEAM_ROLES.map((value) => ({ value, label: CLIENT_TEAM_ROLE_LABELS[value] }));

export function isClientTeamRole(value: unknown): value is ClientTeamRole {
	return typeof value === 'string' && (CLIENT_TEAM_ROLES as readonly string[]).includes(value);
}

/**
 * The words for a role. A value outside the list (a row the API has not migrated yet) is
 * sentence-cased rather than printed raw; no role at all is `null`, so the caller decides what an
 * absent role reads as.
 */
export function clientTeamRoleLabel(role: string | null | undefined): string | null {
	if (isClientTeamRole(role)) return CLIENT_TEAM_ROLE_LABELS[role];
	return role && role.trim() ? sentenceCase(role.trim()) : null;
}

/**
 * A submitted role, read the way the API reads it: blank means the default (`adviser`), one of the
 * four is itself, and anything else is `null` so the caller can refuse it before a round trip.
 */
export function readClientTeamRole(raw: unknown): ClientTeamRole | null {
	const value = typeof raw === 'string' ? raw.trim() : raw == null ? '' : null;
	if (value === null) return null;
	if (value === '') return DEFAULT_CLIENT_TEAM_ROLE;
	return isClientTeamRole(value) ? value : null;
}

/** The team with the adviser first; everyone else keeps the order they came in. */
export function orderClientTeam<T extends { role?: string | null }>(team: readonly T[]): T[] {
	return [
		...team.filter((member) => member.role === 'adviser'),
		...team.filter((member) => member.role !== 'adviser')
	];
}

/** The client's adviser, if they have one. */
export function clientAdviser<T extends { role?: string | null }>(
	team: readonly T[]
): T | undefined {
	return team.find((member) => member.role === 'adviser');
}

const hasActivePlatform = (member: FirmMemberOption) =>
	member.portals.some((p) => p.portal === 'platform' && p.status === 'active');

/**
 * Whether the firm's member is STAFF (`auth.users.type`, sent as both `types` and `type`). A payload
 * with neither (an older API) leaves only platform access to go on, which is what the picker used
 * before.
 */
const isStaff = (member: FirmMemberOption) => {
	const kinds = member.types ?? member.type;
	return kinds == null ? true : kinds.includes('staff');
};

/**
 * Whether this person may be given this role on a client. The API's rule: `adviser` needs the
 * adviser flag, any other role needs a member of staff (not a client login). Both also need ACTIVE
 * platform access here: a deactivated person keeps their membership row (it is never deleted),
 * and offering them is offering someone who can no longer sign in.
 */
export function canHoldClientTeamRole(member: FirmMemberOption, role: ClientTeamRole): boolean {
	if (!hasActivePlatform(member)) return false;
	return role === 'adviser' ? member.is_adviser === true : isStaff(member);
}

/**
 * The person picker for one role. People already on the team are left out only when they already
 * hold THAT role — moving a paraplanner to support (or making them the adviser) is allowed.
 */
export function clientTeamPickerItems(
	members: readonly FirmMemberOption[],
	team: readonly ClientAdviserRow[],
	role: ClientTeamRole
): { value: string; label: string }[] {
	const holding = new Set(
		team.filter((row) => row.role === role && row.adviser_id).map((row) => row.adviser_id)
	);
	return members
		.filter((m) => canHoldClientTeamRole(m, role) && !holding.has(m.user_id))
		.map((m) => ({
			value: m.user_id,
			label: m.name ? `${m.name} (${m.email})` : (m.email ?? '')
		}));
}

/**
 * The one-line note under the picker when a new adviser would replace the current one. `null` when
 * the role is not `adviser`, when the client has no adviser yet, or when the person picked IS the
 * current adviser.
 */
export function replacedAdviserNote(
	team: readonly ClientAdviserRow[],
	role: ClientTeamRole,
	pickedId?: string | null
): string | null {
	if (role !== 'adviser') return null;
	const current = clientAdviser(team);
	if (!current) return null;
	if (pickedId && current.adviser_id === pickedId) return null;
	const name = current.name ?? current.email ?? 'The current adviser';
	return `${name} stays on the team as ${CLIENT_TEAM_ROLE_LABELS.support}.`;
}
