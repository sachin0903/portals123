import {
	COLOUR_SCHEME_HINT,
	THEME_COOKIE,
	applySecurityHeaders,
	brandingHead,
	crossSiteRefusal,
	decideGate,
	devPrincipal,
	fetchMe,
	gateRefusal,
	installPortalHeader,
	isCrossSiteMutation,
	isNavigationRequest,
	mintPageTrace,
	parseTenantMode,
	parseThemeChoice,
	prefersDarkFromHint,
	recordPageView,
	resolveClientPortalTemplate,
	resolveSession,
	themeAttributes,
	themeHead,
	withTraceParent
} from '@graphene/auth';
import { env } from '$env/dynamic/private';
import type { Handle } from '@sveltejs/kit';
import { apiUrl } from '$lib/api';
import { fetchMyClassification } from '$lib/server/classification';
import { fetchMyGroups } from '$lib/server/groups';
import { resolveViewAs } from '$lib/server/view-as';
import {
	ACTIVE_CONTACT_COOKIE,
	DEV_AUTH_COOKIE,
	SESSION_COOKIE,
	VIEW_AS_COOKIE,
	cookieOptions,
	resolveAuth
} from '$lib/server/workos';

/** Which portal THIS app is. A session GET /me confirms for a different one is refused. */
const THIS_PORTAL = 'client';

// Every call this process makes to the API names the portal, including the ones that go
// through global fetch rather than event.fetch. See @graphene/auth `portal-header`.
installPortalHeader(THIS_PORTAL, apiUrl);

export const handle: Handle = async (input) => {
	const page = mintPageTrace();
	input.event.locals.traceId = page.traceId;
	input.event.locals.spanId = page.spanId;
	return await withTraceParent(page.traceparent, () => handleRequest(input));
};

const handleRequest: Handle = async ({ event, resolve }) => {
	event.locals.user = null;
	event.locals.accessToken = null;
	event.locals.organizationId = null;
	event.locals.role = null;
	event.locals.classification = null;
	event.locals.groups = [];
	event.locals.viewAs = null;
	event.locals.portal = undefined;
	event.locals.gate = 'allow';

	// A state-changing request sent by a page on another site is refused before anything is
	// resolved for it. SvelteKit guards form-encoded actions itself; this covers the JSON
	// `+server.ts` mutations too (see @graphene/auth `csrf.ts`). Sec-Fetch-Site first, Origin second.
	if (isCrossSiteMutation(event.request, event.url.origin)) {
		const refusal = crossSiteRefusal();
		applySecurityHeaders(refusal, {
			csp: env.CSP_POLICY,
			cspReportOnly: env.CSP_REPORT_ONLY === 'true'
		});
		return refusal;
	}

	// Host-keyed: resolve which tenant's WorkOS environment this request belongs to
	// (falls back to the single configured environment in local dev). The 60s env-config
	// cache is used for every request — branding edits live on the platform studio, which
	// bypasses there. A client-portal paint may lag the studio by up to the TTL.
	const auth = await resolveAuth(event.request.headers.get('host') ?? '', event.fetch);
	event.locals.auth = auth;
	event.locals.branding = auth.branding ?? null;
	event.locals.appName = auth.appName ?? null;

	// DEV-ONLY: a `dev-auth` cookie carrying a `dev:<base64url-json>` principal, accepted under the
	// SAME four guards as the API's `_dev_bypass_allowed` (flag, stage allowlist, loopback peer,
	// unproxied request) — `@graphene/auth`'s `devPrincipal` is the one implementation. Lets the E2E
	// suite drive authenticated pages without a WorkOS login. Never active in production.
	const dev = devPrincipal(event.cookies.get(DEV_AUTH_COOKIE), {
		flag: env.DEMO_AUTH_BYPASS,
		stage: env.APP_STAGE,
		clientAddress: () => event.getClientAddress(),
		headers: event.request.headers
	});
	const sessionData = event.cookies.get(SESSION_COOKIE);
	// WorkOS's id for a real session, kept only for the wrong-portal refusal below to revoke.
	let sessionId: string | undefined;

	// A member of staff viewing this portal as one of their clients. The cookie holds the opaque
	// session token the API minted when the handoff ticket was redeemed (`routes/view-as`), and
	// is asked about on EVERY request, so a session that has been ended or has lapsed stops
	// working on the next page load rather than when the cookie does. It is resolved before the
	// WorkOS session on purpose: the staff tab may share a host with a client's own cookie, and
	// the view-as session must win while it is live. Not on `/view-as*` itself — those routes
	// mint and end the cookie, and must not be answered by what it currently holds.
	const viewAsToken = event.cookies.get(VIEW_AS_COOKIE);
	const onViewAsRoute =
		event.url.pathname === '/view-as' || event.url.pathname.startsWith('/view-as/');
	if (viewAsToken && !onViewAsRoute) {
		const viewing = await resolveViewAs(viewAsToken, event.fetch);
		if (viewing) {
			event.locals.viewAs = viewing;
			event.locals.accessToken = viewAsToken;
			// A stand-in for the layout's `data.user` and the shell's account menu: this portal is
			// rendered FOR the client, so what the chrome shows is who is being looked at. The
			// adviser's own identity is on `viewAs` and in every audit row the session writes.
			event.locals.user = {
				id: viewing.uid,
				email: viewing.contactName,
				firstName: viewing.contactName,
				lastName: ''
			} as unknown as typeof event.locals.user;
			event.locals.portal = THIS_PORTAL;
		} else {
			// Whatever it was, it is not a session any more.
			event.cookies.delete(VIEW_AS_COOKIE, { path: '/' });
		}
	}

	if (event.locals.viewAs || onViewAsRoute) {
		// A live view-as session, or the routes that mint and end it. Do not authenticate the
		// WorkOS cookie here. These routes are excluded from the view-as cookie on purpose, and
		// falling through used to pick up whatever other session shares this host — on localhost
		// the port is not part of a cookie, so that is the adviser portal's login — decide it
		// was the wrong portal, and revoke it. Leaving then signed the adviser out as well.
	} else if (dev) {
		event.locals.user = {
			id: dev.sub,
			email: dev.email ?? dev.sub,
			firstName: dev.first_name ?? 'Dev',
			lastName: dev.last_name ?? 'User'
		} as unknown as typeof event.locals.user;
		event.locals.accessToken = event.cookies.get(DEV_AUTH_COOKIE) ?? null;
		event.locals.organizationId = dev.org_id ?? null;
		event.locals.role = dev.role ?? null;
	} else if (sessionData) {
		// Authenticate, refreshing reactively (expired) or proactively (near expiry) so downstream
		// API calls never receive a token about to cross the refresh boundary. See resolveSession.
		const { result, sealedSession, clearCookie } = await resolveSession(
			(data) =>
				auth.workos.userManagement.loadSealedSession({
					sessionData: data,
					cookiePassword: auth.cookiePassword
				}),
			sessionData
		);
		if (sealedSession) event.cookies.set(SESSION_COOKIE, sealedSession, cookieOptions);
		if (clearCookie) event.cookies.delete(SESSION_COOKIE, { path: '/' });

		if (result.authenticated) {
			sessionId = result.sessionId;
			event.locals.user = result.user as typeof event.locals.user;
			event.locals.accessToken = result.accessToken ?? null;
			event.locals.organizationId = result.organizationId ?? null;
			event.locals.role = result.role ?? null;
			// `/me` on every request: the wrong-portal gate below needs the portal answer for
			// endpoints too. Cached per token in @graphene/auth (`PERMISSIONS_TTL_MS`, 20s).
			const me = await fetchMe(event.locals.accessToken, { apiUrl: apiUrl(), fetch: event.fetch });
			event.locals.portal = me.portal;
		}
	}

	// The book (classification) and the client's groups shape what a PAGE renders — its design
	// set, its composition, the money-movement gate — and nothing an endpoint proxy answers. So
	// they are resolved for navigations only (also cached per token for 10s): the bell's poll,
	// the SSE ticket and every file relay used to pay this lookup too, uncached, on each call.
	const navigation = isNavigationRequest(event.request, event.isDataRequest);
	if (event.locals.accessToken && navigation) {
		// The book follows the contact on screen. The cookie is what the switcher writes; the
		// layout still validates it against the linked contacts, and `/mine` refuses an id the
		// session does not hold. Absent, the API resolves the login's own contact.
		const raw = event.cookies.get(ACTIVE_CONTACT_COOKIE);
		const contactId = raw !== undefined && /^[1-9]\d*$/.test(raw) ? Number(raw) : null;
		const audience = {
			apiUrl: apiUrl(),
			fetch: event.fetch,
			contactId
		};
		const [classification, groups] = await Promise.all([
			fetchMyClassification(event.locals.accessToken, audience),
			fetchMyGroups(event.locals.accessToken, audience)
		]);
		event.locals.classification = classification;
		event.locals.groups = groups;
	}

	// Wrong-portal is decided HERE so it covers every request, `+server.ts` endpoints included
	// (a layout `load` never runs for those, so the proxies used to serve a session the pages
	// beside them refused). See @graphene/auth `gates.ts`. The login terms gate used to sit beside
	// it and call `GET /terms/outstanding` on every request; that check was removed — terms are
	// collected inside a journey, not on navigation. Navigations are left to the layout's
	// `redirect()`; anything else is answered 403.
	const gated = Boolean(event.locals.user && event.locals.accessToken);
	event.locals.gate = decideGate({
		accessToken: gated ? event.locals.accessToken : null,
		portal: event.locals.portal,
		thisPortal: THIS_PORTAL
	});
	if (event.locals.gate === 'wrong_portal') {
		// A session confirmed for another portal is ended, not merely forgotten: the cookie goes,
		// and WorkOS is told to revoke the session so the same sealed cookie cannot be replayed.
		if (sessionId) {
			try {
				await auth.workos.userManagement.revokeSession({ sessionId });
			} catch {
				// Best-effort: the cookie is cleared regardless, and the token expires on its own.
			}
		}
		event.cookies.delete(SESSION_COOKIE, { path: '/' });
		event.locals.user = null;
		event.locals.accessToken = null;
	}
	if (event.locals.gate !== 'allow' && !navigation) {
		const refusal = gateRefusal(event.locals.gate);
		applySecurityHeaders(refusal, {
			csp: env.CSP_POLICY,
			cspReportOnly: env.CSP_REPORT_ONLY === 'true'
		});
		return refusal;
	}

	// A firm's client portal can carry several named design sets. Resolve the authenticated
	// member's groups, then their book (classification), after the sealed session has been
	// opened; unauthenticated pages keep the firm's shared identity/login branding. Auth-profile
	// roles remain a fallback for firms that have not moved off them. The result is also what
	// page loaders read, so the navigation, page composition and colour scheme switch together.
	const effectiveBranding = event.locals.user
		? resolveClientPortalTemplate(auth.branding, {
				groups: event.locals.groups,
				classification: event.locals.classification?.slug,
				role: event.locals.role
			})
		: (auth.branding ?? null);
	event.locals.branding = effectiveBranding;

	// White-label the document head server-side (no flash): override the theme CSS vars, favicon
	// and title before %sveltekit.head%. Values are re-validated + escaped in brandingHead.
	// Exactly one favicon. app.html deliberately carries no static icon: two rel=icon links leave
	// the browser free to keep the product SVG even after a firm uploads its own PNG.
	const head = brandingHead(effectiveBranding, auth.appName, {
		fallbackFavicon: '/favicon.svg'
	});

	// Colour scheme, resolved server-side so the FIRST paint is already right: the user's explicit
	// choice (the `gp-theme` cookie the ThemeToggle writes) beats the tenant's branding mode, which
	// beats the OS. `%theme.attrs%` puts `class="dark"` on <html> when that is decidable here;
	// `%theme.head%` is the blocking script that settles the `system` case (and re-decides after
	// brandingHead's own tenant-mode script, which is why it is injected second). See @graphene/auth.
	const theme = {
		choice: parseThemeChoice(event.cookies.get(THEME_COOKIE)),
		mode: parseTenantMode(effectiveBranding?.mode),
		prefersDark: prefersDarkFromHint(event.request.headers.get(COLOUR_SCHEME_HINT))
	};

	const response = await resolve(event, {
		transformPageChunk: ({ html }) =>
			html
				.replace('%tenant.head%', head)
				.replace('%theme.attrs%', themeAttributes(theme))
				.replace('%theme.head%', themeHead())
	});
	// Ask for the OS colour-scheme client hint, so from the next request on even a `system` user's
	// scheme is decided server-side rather than by the inline script. HTML responses only — assets
	// must not pick up a `Vary` that fragments their cache.
	if (response.headers.get('content-type')?.startsWith('text/html')) {
		response.headers.set('Accept-CH', COLOUR_SCHEME_HINT);
		response.headers.append('Vary', COLOUR_SCHEME_HINT);
	}
	// Baseline security headers on every response (framing/MIME/referer/permissions). The CSP is
	// env-configurable (CSP_POLICY / CSP_REPORT_ONLY) so a fuller policy can be trialled without a
	// code deploy; the default is frame-ancestors only. The camera is allowed for this origin
	// because onboarding takes the identity-verification photos in the page. See @graphene/auth.
	applySecurityHeaders(response, {
		csp: env.CSP_POLICY,
		cspReportOnly: env.CSP_REPORT_ONLY === 'true',
		allowCamera: true
	});
	// Not for a view-as session: the page views are the client's engagement record, and an
	// adviser looking in is not the client (the API would refuse the POST regardless).
	if (!event.locals.viewAs) recordPageView(event, response, { apiUrl: apiUrl() });
	return response;
};
