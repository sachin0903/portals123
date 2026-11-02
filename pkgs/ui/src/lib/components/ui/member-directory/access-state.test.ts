import { describe, expect, it } from 'vitest';
import { isAwaitingInvite, isSuspendedMember } from './access-state.js';

describe('isSuspendedMember', () => {
	it('is false for someone who has been invited and has not accepted yet', () => {
		// THE BUG. These menus keyed off `enabled`, which the invite sets from WorkOS's
		// `email_verified` — false for every outstanding invite. So a client invited seconds
		// earlier was offered "Reactivate", and WorkOS refuses to reactivate a pending membership:
		// the only state control on the row could not work. They want Resend invitation.
		expect(isSuspendedMember([{ status: 'pending' }])).toBe(false);
		expect(isAwaitingInvite([{ status: 'pending' }])).toBe(true);
	});

	it('is true once the membership is inactive', () => {
		expect(isSuspendedMember([{ status: 'inactive' }])).toBe(true);
		expect(isAwaitingInvite([{ status: 'inactive' }])).toBe(false);
	});

	it('is false for an active member', () => {
		expect(isSuspendedMember([{ status: 'active' }])).toBe(false);
	});

	it('takes a partly-revoked person as not suspended', () => {
		// One portal deliberately revoked is not a suspension: they still hold the other.
		expect(isSuspendedMember([{ status: 'inactive' }, { status: 'active' }])).toBe(false);
	});

	it('claims nothing about a row with no membership at all', () => {
		// A placeholder the provider sync minted holds no portal — neither control applies.
		expect(isSuspendedMember([])).toBe(false);
		expect(isSuspendedMember(undefined)).toBe(false);
	});
});
