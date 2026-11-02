import { passwordAuth as run, type PasswordAuthResult, type SignInClient } from '@graphene/auth';
import type { AuthContext } from './workos';

export type { PasswordAuthResult };

/** Authenticate with email + password against the request's resolved WorkOS environment
 * (host-keyed; the context comes from `locals.auth`, set in `hooks.server.ts`).
 * `client` is forwarded to WorkOS so the session records the browser's IP and user agent. */
export function passwordAuth(
	ctx: AuthContext,
	email: string,
	password: string,
	client?: SignInClient
): Promise<PasswordAuthResult> {
	return run(
		ctx.workos,
		{
			clientId: ctx.clientId,
			cookiePassword: ctx.cookiePassword,
			organizationId: ctx.organizationId
		},
		email,
		password,
		client
	);
}
