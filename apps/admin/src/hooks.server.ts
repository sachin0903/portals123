import {
	COLOUR_SCHEME_HINT,
	THEME_COOKIE,
	applySecurityHeaders,
	fetchMe,
	installPortalHeader,
	mintPageTrace,
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
import { resolveAdminBrand } from '$lib/server/branding';
import { SESSION_COOKIE, cookieOptions, resolveAuth } from '$lib/server/workos';

/** Which portal THIS app is. Named on every API call so a shared application stays here. */
const THIS_PORTAL = 'operations';

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
	// cache is the default; tenant branding routes bypass it so a logo/colour save is on
	// the next paint of that page.
	const pathname = event.url.pathname;
	const onBranding = /\/tenants\/[^/]+\/branding(?:\/|$)/.test(pathname);
	const auth = await resolveAuth(event.request.headers.get('host') ?? '', event.fetch, {
		bypassCache: onBranding
	});
	event.locals.auth = auth;

	// The shell's brand: the Graphene (operations) tenant's own logo set — the dark/light logos
	// edited at /tenants/{id}/branding — read via the internal endpoint with a 60s in-process
	// cache (see $lib/server/branding). ONLY logos/icon/name ride in; the admin surface keeps
	// the product look (no tenant colour theming), and null falls back to the static brand.
	event.locals.brand = await resolveAdminBrand(event.fetch, { bypassCache: onBranding });

	const sessionData = event.cookies.get(SESSION_COOKIE);
	if (sessionData) {
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

	// Colour scheme, resolved server-side so the FIRST paint is already right: the user's explicit
	// choice (the `gp-theme` cookie the ThemeToggle writes), else the OS. There is no tenant mode
	// here — the admin surface stays Graphene-branded — so `mode` is left unset (auto).
	// `%theme.head%` is the blocking script that settles the `system` case. See @graphene/auth.
	const theme = {
		choice: parseThemeChoice(event.cookies.get(THEME_COOKIE)),
		prefersDark: prefersDarkFromHint(event.request.headers.get(COLOUR_SCHEME_HINT))
	};

	const response = await resolve(event, {
		transformPageChunk: ({ html }) =>
			html.replace('%theme.attrs%', themeAttributes(theme)).replace('%theme.head%', themeHead())
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
