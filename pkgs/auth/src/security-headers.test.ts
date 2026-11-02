import { describe, expect, it } from 'vitest';
import { applySecurityHeaders, DEFAULT_CSP } from './security-headers';

describe('applySecurityHeaders', () => {
	it('sets the baseline security headers with the default CSP', () => {
		const res = new Response('ok');
		applySecurityHeaders(res);
		expect(res.headers.get('Content-Security-Policy')).toBe(DEFAULT_CSP);
		expect(res.headers.get('Content-Security-Policy')).toBe("frame-ancestors 'self'");
		expect(res.headers.get('X-Frame-Options')).toBe('SAMEORIGIN');
		expect(res.headers.get('X-Content-Type-Options')).toBe('nosniff');
		expect(res.headers.get('Referrer-Policy')).toBe('strict-origin-when-cross-origin');
		expect(res.headers.get('Permissions-Policy')).toContain('geolocation=()');
	});

	it('denies the camera by default', () => {
		const res = new Response('ok');
		applySecurityHeaders(res);
		expect(res.headers.get('Permissions-Policy')).toBe(
			'camera=(), microphone=(), geolocation=(), interest-cohort=()'
		);
	});

	it('allows the camera for this origin only when allowCamera is set', () => {
		const res = new Response('ok');
		applySecurityHeaders(res, { allowCamera: true });
		expect(res.headers.get('Permissions-Policy')).toBe(
			'camera=(self), microphone=(), geolocation=(), interest-cohort=()'
		);
	});

	it('does NOT set HSTS (that belongs at the edge)', () => {
		const res = new Response('ok');
		applySecurityHeaders(res);
		expect(res.headers.get('Strict-Transport-Security')).toBeNull();
	});

	it('does not overwrite a route-set X-Frame-Options', () => {
		const res = new Response('ok', { headers: { 'X-Frame-Options': 'DENY' } });
		applySecurityHeaders(res);
		expect(res.headers.get('X-Frame-Options')).toBe('DENY');
	});

	it('uses a caller-supplied CSP when given', () => {
		const res = new Response('ok');
		applySecurityHeaders(res, { csp: "default-src 'self'; frame-ancestors 'self'" });
		expect(res.headers.get('Content-Security-Policy')).toBe(
			"default-src 'self'; frame-ancestors 'self'"
		);
	});

	it('falls back to the default CSP for a blank/whitespace value', () => {
		for (const csp of ['', '   ', null, undefined]) {
			const res = new Response('ok');
			applySecurityHeaders(res, { csp });
			expect(res.headers.get('Content-Security-Policy')).toBe(DEFAULT_CSP);
		}
	});

	it('report-only sends the policy under the Report-Only header and NOT the enforcing one', () => {
		const res = new Response('ok');
		applySecurityHeaders(res, { csp: "default-src 'self'", cspReportOnly: true });
		expect(res.headers.get('Content-Security-Policy-Report-Only')).toBe("default-src 'self'");
		expect(res.headers.get('Content-Security-Policy')).toBeNull();
	});

	it('report-only never reopens clickjacking — X-Frame-Options: SAMEORIGIN is always enforced', () => {
		const res = new Response('ok');
		applySecurityHeaders(res, { csp: "default-src 'self'", cspReportOnly: true });
		expect(res.headers.get('X-Frame-Options')).toBe('SAMEORIGIN');
	});

	it('keeps an existing CSP (the /_assets sandbox) and folds frame-ancestors into it', () => {
		const sandbox = "default-src 'none'; style-src 'unsafe-inline'; sandbox";
		const res = new Response('ok', { headers: { 'Content-Security-Policy': sandbox } });
		applySecurityHeaders(res);
		const csp = res.headers.get('Content-Security-Policy') ?? '';
		expect(csp).toContain("default-src 'none'");
		expect(csp).toContain('sandbox');
		expect(csp).toContain("frame-ancestors 'self'");
	});

	it('does not duplicate frame-ancestors on an existing CSP that already has one', () => {
		const existing = "default-src 'none'; frame-ancestors 'none'; sandbox";
		const res = new Response('ok', { headers: { 'Content-Security-Policy': existing } });
		applySecurityHeaders(res);
		expect(res.headers.get('Content-Security-Policy')).toBe(existing);
	});
});
