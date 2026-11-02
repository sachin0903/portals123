import { describe, expect, it } from 'vitest';
import type { Member } from './api';
import {
	accountState,
	anyPending,
	pendingPortal,
	portalOf,
	revokedPortal,
	syncMessage
} from './members';

const member = (
	enabled: boolean,
	portals: { portal: string; status: string; role_slug?: string | null }[]
): Member =>
	({
		user_id: 'u_1',
		email: 'p@firm.test',
		name: 'A Person',
		enabled,
		portals: portals.map((p) => ({ role_slug: null, ...p }))
	}) as Member;

describe('portalOf', () => {
	it('returns the membership for a portal the person actively holds', () => {
		const m = member(true, [{ portal: 'platform', status: 'active', role_slug: 'adviser' }]);
		expect(portalOf(m, 'platform')?.role_slug).toBe('adviser');
	});

	it('treats a pending membership as held — it is granted, just not onboarded', () => {
		const m = member(false, [{ portal: 'client', status: 'pending' }]);
		expect(portalOf(m, 'client')?.status).toBe('pending');
	});

	it('does NOT treat a revoked membership as held', () => {
		// The row survives revocation so the trail isn't lost. Reading it as held made the cell
		// offer "Revoke" on something already revoked, and never offer a way back.
		const m = member(true, [{ portal: 'client', status: 'inactive' }]);
		expect(portalOf(m, 'client')).toBeNull();
		expect(revokedPortal(m, 'client')).toBe(true);
	});

	it('returns null for a portal that was never granted, which is not "revoked" either', () => {
		const m = member(true, [{ portal: 'platform', status: 'active' }]);
		expect(portalOf(m, 'client')).toBeNull();
		expect(revokedPortal(m, 'client')).toBe(false);
	});

	it('keeps the two portals independent — a person may hold either or both', () => {
		const m = member(true, [
			{ portal: 'client', status: 'inactive' },
			{ portal: 'platform', status: 'active' }
		]);
		expect(portalOf(m, 'client')).toBeNull();
		expect(portalOf(m, 'platform')?.status).toBe('active');
	});
});

describe('accountState', () => {
	it('is active for an onboarded, enabled person', () => {
		expect(accountState(member(true, [{ portal: 'platform', status: 'active' }]))).toBe('active');
	});

	it('is invited — not suspended — for a fresh invite', () => {
		// `enabled` is false for BOTH "deliberately suspended" and "hasn't set a password yet", so a
		// straight read of !enabled put a red Suspended badge on every new invite.
		expect(accountState(member(false, [{ portal: 'platform', status: 'pending' }]))).toBe(
			'invited'
		);
	});

	it('is suspended when disabled with no membership still awaiting onboarding', () => {
		expect(accountState(member(false, [{ portal: 'platform', status: 'inactive' }]))).toBe(
			'suspended'
		);
	});

	it('prefers invited when one portal is pending and another was revoked', () => {
		const m = member(false, [
			{ portal: 'client', status: 'inactive' },
			{ portal: 'platform', status: 'pending' }
		]);
		expect(anyPending(m)).toBe(true);
		expect(accountState(m)).toBe('invited');
	});

	it('is suspended for someone disabled with no portals at all', () => {
		expect(accountState(member(false, []))).toBe('suspended');
	});
});

describe('pendingPortal', () => {
	it('names the portal a resend should target, not merely the first membership', () => {
		const m = member(false, [
			{ portal: 'client', status: 'active' },
			{ portal: 'platform', status: 'pending' }
		]);
		expect(pendingPortal(m)).toBe('platform');
	});

	it('is empty when nothing is pending, so no resend is offered', () => {
		expect(pendingPortal(member(true, [{ portal: 'client', status: 'active' }]))).toBe('');
	});
});

// ---------------------------------------------------------------------------------------------
// The sync toast
//
// A real sync of a real firm reported "Synced 33 member(s) from WorkOS." while the members table
// showed the same 7 people it had before, because 26 of the 33 held no portal row and the listing
// inner-joined the memberships. Nothing was broken; nothing was legible either.
// ---------------------------------------------------------------------------------------------

describe('syncMessage', () => {
	it('names the import and the people it left without access', () => {
		expect(syncMessage({ members: 33, imported: 26, withoutPortal: 26 })).toBe(
			'Synced 33 from WorkOS. 26 newly mirrored. 26 hold no portal access yet.'
		);
	});

	it('says nothing about zero counts, so a clean sync stays one short sentence', () => {
		expect(syncMessage({ members: 7, imported: 0, withoutPortal: 0 })).toBe(
			'Synced 7 from WorkOS.'
		);
	});

	it('reports the portal-less alone when nothing new arrived — the second and later syncs', () => {
		expect(syncMessage({ members: 33, imported: 0, withoutPortal: 26 })).toBe(
			'Synced 33 from WorkOS. 26 hold no portal access yet.'
		);
	});

	it('survives a missing payload rather than rendering NaN into a toast', () => {
		expect(syncMessage(null)).toBe('Synced 0 from WorkOS.');
		expect(syncMessage(undefined)).toBe('Synced 0 from WorkOS.');
		expect(syncMessage({})).toBe('Synced 0 from WorkOS.');
	});
});
