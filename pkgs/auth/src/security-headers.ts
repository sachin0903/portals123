/**
 * Baseline HTTP security headers, applied to every portal response.
 *
 * Defined once and called from each portal's `hooks.server.ts` so the three portals cannot drift
 * (the same reason `brandingHead`/`resolveSession` live here). Every header set here is
 * script-agnostic — it constrains framing, MIME-sniffing, referer leakage and unused browser APIs —
 * so it is safe on any response and never interferes with the inline theme/branding `<script>`s the
 * hooks inject via `%theme.head%`.
 *
 * Deliberately NOT set here:
 *  - A full `script-src` CSP. That needs SvelteKit's nonce/hash machinery (`kit.csp`) to cover the
 *    injected inline scripts, or it breaks the no-flash theme — a separate change. The default CSP
 *    below is only the `frame-ancestors` directive (it constrains framing alone, never
 *    scripts/styles). It is OVERRIDABLE per environment (see `SecurityHeadersOptions.csp`) so a
 *    fuller policy can be trialled without a code change.
 *  - `Strict-Transport-Security` (HSTS). It belongs at the edge (Caddy/Coolify), set there.
 *
 * This package is framework-agnostic and must not read `$env`, so the caller (each app's hooks)
 * reads its own environment and passes the CSP config in — the same pattern `resolveAuth` uses.
 */

/** The enforced CSP when the caller passes none: same-origin framing only, safe with inline scripts. */
export const DEFAULT_CSP = "frame-ancestors 'self'";

export interface SecurityHeadersOptions {
	/**
	 * The Content-Security-Policy value. Defaults to {@link DEFAULT_CSP} (`frame-ancestors 'self'`).
	 * Override per environment (read from env by the caller) to trial a fuller policy — e.g. a
	 * Phase-2 `script-src` policy — without a code change. A blank/whitespace value falls back to the
	 * default rather than emitting an empty CSP.
	 */
	csp?: string | null;
	/**
	 * When true the CSP is sent as `Content-Security-Policy-Report-Only`: the browser REPORTS
	 * violations of it but enforces nothing, which is how a new policy is tuned safely. Clickjacking
	 * stays blocked regardless, because `X-Frame-Options: SAMEORIGIN` (below) is always enforced — so
	 * flipping report-only on to test a `script-src` policy never reopens the framing hole to other
	 * origins.
	 */
	cspReportOnly?: boolean;
	/**
	 * When true the Permissions-Policy grants the camera to this origin only (`camera=(self)`), so a
	 * page can open the device camera with `getUserMedia`. Off by default: only the client portal
	 * passes it, for the identity-verification photo capture. Cross-origin frames stay denied.
	 */
	allowCamera?: boolean;
}

function hasFrameAncestors(policy: string): boolean {
	return /(?:^|;)\s*frame-ancestors\b/i.test(policy);
}

function withFrameAncestors(policy: string): string {
	if (hasFrameAncestors(policy)) return policy;
	return `${policy.replace(/;?\s*$/, '')}; ${DEFAULT_CSP}`;
}

export function applySecurityHeaders(response: Response, opts: SecurityHeadersOptions = {}): void {
	const h = response.headers;
	// Clickjacking. External sites must not frame us; same-origin iframes are deliberate (admin
	// template PDF preview, DocumentViewer HTML). `X-Frame-Options: SAMEORIGIN` is the always-enforced
	// backstop (universally supported and unaffected by the CSP being in report-only mode); the CSP's
	// `frame-ancestors` is the modern equivalent for browsers that honour it.
	const existingCsp = h.get('Content-Security-Policy');
	if (existingCsp) {
		// Route handlers that already set a CSP (the `/_assets` sandbox in particular) keep it.
		// Replacing it with framing-only would drop `sandbox` / `default-src 'none'` on SVG relays.
		h.set('Content-Security-Policy', withFrameAncestors(existingCsp));
	} else {
		const csp = opts.csp?.trim() || DEFAULT_CSP;
		const cspHeader = opts.cspReportOnly
			? 'Content-Security-Policy-Report-Only'
			: 'Content-Security-Policy';
		h.set(cspHeader, csp);
	}
	if (!h.has('X-Frame-Options')) {
		h.set('X-Frame-Options', 'SAMEORIGIN');
	}
	// Stop MIME-sniffing so an upload/download served with the wrong type is not reinterpreted.
	if (!h.has('X-Content-Type-Options')) {
		h.set('X-Content-Type-Options', 'nosniff');
	}
	// Don't leak full URLs (which can carry public uids) to third-party origins via the Referer.
	h.set('Referrer-Policy', 'strict-origin-when-cross-origin');
	// Switch off browser features the portals never use. The camera is the one opt-in (see
	// `allowCamera`), and only for this origin.
	const camera = opts.allowCamera ? 'camera=(self)' : 'camera=()';
	h.set('Permissions-Policy', `${camera}, microphone=(), geolocation=(), interest-cohort=()`);
}
