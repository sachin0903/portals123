/**
 * What a person's access state IS, for the surfaces that let somebody change it — the platform
 * portal's Team and Profiles (listing and record) and the admin portal's tenant members and
 * operations staff. Pure, and shared, because four menus reading four signals is how two of them
 * came to offer an action the vendor can only refuse.
 */

/**
 * Whether a person is SUSPENDED — the one state Reactivate can restore.
 *
 * Deliberately NOT `auth.users.enabled`, which is what most of these menus used to read: the
 * invite sets it from WorkOS's `email_verified`, so it is false for everyone who has been invited
 * and has not yet set a password. A client invited five seconds ago therefore rendered as
 * "Reactivate", and pressing it asked WorkOS to reactivate a `pending` membership — which the
 * vendor refuses outright ("the invite must be accepted instead"), so the only state control on
 * the row could never work. What that person needs is Resend invitation.
 *
 * Suspension is every membership being `inactive`; `pending` is somebody on their way IN, and one
 * portal of several being revoked is not a suspension of the person.
 */
export function isSuspendedMember(memberships: readonly { status: string }[] | undefined): boolean {
	const rows = memberships ?? [];
	return rows.length > 0 && rows.every((m) => m.status === 'inactive');
}

/** Whether an invitation is still outstanding: invited, not yet accepted. */
export function isAwaitingInvite(memberships: readonly { status: string }[] | undefined): boolean {
	return (memberships ?? []).some((m) => m.status === 'pending');
}
