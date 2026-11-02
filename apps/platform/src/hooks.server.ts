import {
	COLOUR_SCHEME_HINT,
	THEME_COOKIE,
	applySecurityHeaders,
	brandingHead,
	devPrincipal,
	fetchMe,
	installPortalHeader,
	mintPageTrace,
	parseTenantMode,
	parseThemeChoice,
	prefersDarkFromHint,
	recordPageView,
	resolveSession,
	themeAttributes,
	themeHead,
	withTraceParent
} from '@graphene/auth';
import { env } from '$env/dynamic/private';
import type { Handle } from '@sveltejs/kit';
import { apiUrl } from '$lib/api';
import { DEV_AUTH_COOKIE, SESSION_COOKIE, cookieOptions, resolveAuth } from '$lib/server/workos';

/** Which portal THIS app is. Named on every API call so a shared application stays here. */
const THIS_PORTAL = 'platform';

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
	event.locals.permissions = [];
	event.locals.portal = undefined;

	// Host-keyed: resolve which tenant's WorkOS environment this request belongs to
	// (falls back to the single configured environment in local dev). The 60s env-config
	// cache is the default; only the branding studio bypasses it so a colour save paints
	// on the next load of that page instead of waiting out the TTL.
	const pathname = event.url.pathname;
	const auth = await resolveAuth(event.request.headers.get('host') ?? '', event.fetch, {
		bypassCache: pathname === '/branding' || pathname.startsWith('/branding/')
	});
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
	if (dev) {
		event.locals.user = {
			id: dev.sub,
			email: dev.email ?? dev.sub,
			firstName: dev.first_name ?? 'Dev',
			lastName: dev.last_name ?? 'User'
		} as unknown as typeof event.locals.user;
		event.locals.accessToken = event.cookies.get(DEV_AUTH_COOKIE) ?? null;
		event.locals.organizationId = dev.org_id ?? null;
		event.locals.role = dev.role ?? null;
		event.locals.permissions = dev.permissions ?? [];
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
			event.locals.user = result.user as typeof event.locals.user;
			event.locals.accessToken = result.accessToken ?? null;
			event.locals.organizationId = result.organizationId ?? null;
			event.locals.role = result.role ?? null;
			const me = await fetchMe(event.locals.accessToken, {
				apiUrl: apiUrl(),
				fetch: event.fetch
			});
			event.locals.permissions = me.permissions;
			event.locals.portal = me.portal;
		}
	}

	// White-label the document head server-side (no flash): theme CSS vars, favicon and title
	// before %sveltekit.head%. Values are re-validated + escaped in brandingHead.
	// surface:'fixed' — the firm brands the colours, logo and font, but NOT the surface material.
	// The platform portal ships the sharp preset and a tenant must not be able to put frosted
	// glass and consumer corners back into the staff tool. See sharedDecls in @graphene/auth.
	// Exactly one favicon. app.html deliberately carries no static icon: two rel=icon links leave
	// the browser free to keep the product SVG even after a firm uploads its own PNG.
	const head = brandingHead(auth.branding, auth.appName, {
		surface: 'fixed',
		fallbackFavicon: '/favicon.svg'
	});

	// Colour scheme, resolved server-side so the FIRST paint is already right: the user's explicit
	// choice (the `gp-theme` cookie the ThemeToggle writes) beats the tenant's branding mode, which
	// beats the OS. `%theme.attrs%` puts `class="dark"` on <html> when that is decidable here;
	// `%theme.head%` is the blocking script that settles the `system` case (and re-decides after
	// brandingHead's own tenant-mode script, which is why it is injected second). See @graphene/auth.
	const theme = {
		choice: parseThemeChoice(event.cookies.get(THEME_COOKIE)),
		mode: parseTenantMode(auth.branding?.mode),
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
	// code deploy; the default is frame-ancestors only. See @graphene/auth.
	applySecurityHeaders(response, {
		csp: env.CSP_POLICY,
		cspReportOnly: env.CSP_REPORT_ONLY === 'true'
	});
	recordPageView(event, response, { apiUrl: apiUrl() });
	return response;
};
