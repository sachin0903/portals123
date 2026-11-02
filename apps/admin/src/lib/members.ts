/**
 * Member state derivation for the firm members table.
 *
 * Extracted from the page because it is the non-obvious part and it was getting the labels wrong:
 * a membership row survives revocation (status `inactive`) so history isn't lost, and `enabled` is
 * false for two completely different situations — deliberately suspended, and simply not yet
 * onboarded. Both distinctions decide which actions a row offers, so they belong in one place with
 * tests rather than inline in the markup.
 */

import { isAwaitingInvite, isSuspendedMember } from '@graphene/ui';
import type { Member, MemberPortal } from './api';

/**
 * The membership a person ACTIVELY holds in `portal`, or null.
 *
 * A revoked portal keeps its row (`inactive`) so the trail survives, but for the UI it is not held
 * access — treating it as held made the cell offer "Revoke" on something already revoked and never
 * offer a way back.
 */
export function portalOf(member: Member, portal: string): MemberPortal | null {
	const held = member.portals.find((p) => p.portal === portal);
	return held && held.status !== 'inactive' ? held : null;
}

/** True when this portal was granted and then revoked — the row that can be restored. */
export function revokedPortal(member: Member, portal: string): boolean {
	return member.portals.some((p) => p.portal === portal && p.status === 'inactive');
}

/** True while any of the person's portals is still awaiting onboarding. */
export function anyPending(member: Member): boolean {
	return isAwaitingInvite(member.portals);
}

/** The portal a resend/reveal should target: the one actually awaiting onboarding. */
export function pendingPortal(member: Member): string {
	return member.portals.find((p) => p.status === 'pending')?.portal ?? '';
}

export type AccountState = 'active' | 'invited' | 'suspended';

/**
 * The whole-person badge.
 *
 * `enabled` alone cannot express this: it is false for a deliberately suspended person AND for one
 * who simply hasn't set a password yet, so a straight read of `!enabled` put a red "Suspended"
 * badge next to a "Pending" chip on every fresh invite. A pending membership means invited.
 */
export function accountState(member: Member): AccountState {
	if (anyPending(member)) return 'invited';
	// `isSuspendedMember` is the shared rule (`@graphene/ui`) so that this badge and the row's own
	// Deactivate/Reactivate control cannot disagree about what suspended means. `enabled` remains
	// the tie-breaker for a row with no membership at all — a person the WorkOS sync mirrored and
	// nobody has granted a portal to is not active, and there is no membership to read.
	if (isSuspendedMember(member.portals)) return 'suspended';
	return member.enabled ? 'active' : 'suspended';
}

/** What one "Sync from WorkOS" actually did. */
export type SyncCounts = {
	/** WorkOS org memberships the reconcile walked. */
	members?: number;
	/** People the mirror did not already have. */
	imported?: number;
	/** The firm's people holding NO portal access at all. */
	withoutPortal?: number;
};

/**
 * The sync toast.
 *
 * "Synced 33 member(s) from WorkOS." was true and told an operator nothing. The reconcile mirrors
 * a firm's WorkOS people into `auth.users` but deliberately NEVER invents a portal row — which
 * portals somebody may use is this app's decision, and deriving it from a WorkOS org membership is
 * the org-based classification the identity model forbids. So a sync that imports a whole staff
 * list and grants none of them access is CORRECT, and read as a no-op: 33 members, no visible
 * change, no explanation.
 *
 * The portal-less count is the actionable half. Those people are listed in the table with both
 * portal cells offering a grant — which they were not, until `members_with_portals` stopped
 * inner-joining the memberships it was filtering on.
 */
export function syncMessage(d: SyncCounts | null | undefined): string {
	const seen = Number(d?.members ?? 0);
	const imported = Number(d?.imported ?? 0);
	const unassigned = Number(d?.withoutPortal ?? 0);
	const parts = [`Synced ${seen} from WorkOS`];
	if (imported > 0) parts.push(`${imported} newly mirrored`);
	if (unassigned > 0) parts.push(`${unassigned} hold no portal access yet`);
	return `${parts.join('. ')}.`;
}
