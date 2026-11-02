import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('client logout', () => {
	const source = readFileSync(new URL('./+page.server.ts', import.meta.url), 'utf8');

	it('clears the active-contact cookie with the session', () => {
		expect(source).toContain('ACTIVE_CONTACT_COOKIE');
		expect(source).toContain('cookies.delete(ACTIVE_CONTACT_COOKIE, cookieOptions)');
	});

	it('clears the dev bypass cookie too, or sign-out is a no-op while the bypass is on', () => {
		expect(source).toContain("cookies.delete(DEV_AUTH_COOKIE, { path: '/' })");
	});
});
