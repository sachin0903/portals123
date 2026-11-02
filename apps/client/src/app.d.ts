// See https://svelte.dev/docs/kit/types#app.d.ts
import type { User } from '@workos-inc/node';
import type { Branding, GateDecision } from '@graphene/auth';
import type { ClientClassification } from '$lib/server/classification';
import type { ViewAsSession } from '$lib/server/view-as';

declare global {
	namespace App {
		// interface Error {}
		interface Locals {
			user: User | null;
			accessToken: string | null;
			organizationId: string | null;
			role: string | null;
			/** Set when a member of staff is viewing this portal as one of their clients: the
			 *  live session (`GET /view-as/session`) the `gp-view-as-client` cookie resolved to.
			 *  `accessToken` is then the view-as token and `user` a stand-in for the adviser. */
			viewAs: ViewAsSession | null;
			/** Resolved book label for this signed-in client. Null when the firm has none. */
			classification: ClientClassification | null;
			/** This client's group slugs, in catalogue order. Empty when they wear none. */
			groups: string[];
			// Confirmed portal for this session from GET /me. `undefined` = no answer (fail
			// open); `null` = confirmed no membership for this application (refuse).
			portal: string | null | undefined;
			// The post-authentication gate decided in hooks.server.ts (wrong portal).
			// The hook has already answered non-navigation requests; the root layout redirects pages.
			gate: GateDecision;
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
			/** A sub page's own back target (see `$lib/nav::backFor`); the route table otherwise. */
			back?: { href: string; label: string };
			/** Below `lg` the page is the whole screen — no shell top bar, no column padding — and
			 *  draws its own header with the way back in it (ClientShell `immersive`). */
			immersive?: boolean;
		}
		interface PageState {
			/** The account page's Holdings | Activity switch — shallow-routed, so a tab change is
			 *  not a navigation. The URL (`?view=`) carries it for a reload or a shared link. */
			view?: 'holdings' | 'activity';
		}
		// interface Platform {}
	}
}

export {};
