// See https://svelte.dev/docs/kit/types#app.d.ts
import type { User } from '@workos-inc/node';
import type { Branding } from '@graphene/auth';

declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			user: User | null;
			accessToken: string | null;
			organizationId: string | null;
			role: string | null;
			permissions: string[];
			// Confirmed portal for this session from GET /me. `undefined` = no answer (fail
			// open); `null` = confirmed no membership for this application (refuse).
			portal: string | null | undefined;
			// The WorkOS environment resolved for this request's host (project-per-tenant),
			// set in hooks.server.ts. Routes read the client/secrets from here.
			auth: {
				workos: import('@workos-inc/node').WorkOS;
				clientId: string;
				cookiePassword: string;
			};
			// Per-tenant white-label branding (null when unbranded), set in hooks.server.ts.
			branding: Branding | null;
			appName: string | null;
			/** W3C trace for this render. Page data carries it to the browser; it is not a cookie. */
			traceId: string;
			spanId: string;
		}
		interface PageData {
			traceId?: string;
			spanId?: string;
		}
		// interface PageState {}
		// interface Platform {}
	}
}

export {};
