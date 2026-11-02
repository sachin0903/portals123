import type { WorkOS } from '@workos-inc/node';
import { sessionIdFromAccessToken } from './sessions';

export type PortalSessionResult = 'allowed' | 'denied' | 'unavailable';

export interface PortalSessionOptions {
	apiUrl: string;
	fetch?: typeof fetch;
}

/**
 * Confirm that a newly-minted WorkOS session resolves to an active membership for this portal
 * before a browser cookie is written. WorkOS authenticates the identity even when its org
 * membership has been deactivated; `GET /me/portal` applies the app-owned membership status.
 *
 * Fail closed. A session whose access cannot be confirmed is revoked best-effort, so an API outage
 * or a deactivated account never leaves a discarded live WorkOS session behind.
 */
export async function validatePortalSession(
	workos: WorkOS,
	accessToken: string | undefined,
	expectedPortal: 'client' | 'platform' | 'operations',
	opts: PortalSessionOptions
): Promise<PortalSessionResult> {
	let result: PortalSessionResult = 'unavailable';
	if (accessToken && opts.apiUrl) {
		try {
			const response = await (opts.fetch ?? fetch)(`${opts.apiUrl}/me/portal`, {
				headers: {
					Authorization: `Bearer ${accessToken}`,
					'x-graphene-portal': expectedPortal
				}
			});
			if (response.ok) {
				const body = (await response.json()) as { portal?: unknown };
				result = body.portal === expectedPortal ? 'allowed' : 'denied';
			}
		} catch {
			result = 'unavailable';
		}
	}
	if (result !== 'allowed' && accessToken) {
		const sessionId = sessionIdFromAccessToken(accessToken);
		if (sessionId) {
			try {
				await workos.userManagement.revokeSession({ sessionId });
			} catch {
				// The application session is still refused. Revocation failure is best-effort.
			}
		}
	}
	return result;
}
