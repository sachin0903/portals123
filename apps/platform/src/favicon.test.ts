import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const template = readFileSync(new URL('./app.html', import.meta.url), 'utf8');
const hook = readFileSync(new URL('./hooks.server.ts', import.meta.url), 'utf8');

describe('tenant favicon', () => {
	it('has one owner: the server-injected brand head with its product fallback', () => {
		expect(template).not.toMatch(/rel=["']icon["']/);
		expect(hook).toContain("fallbackFavicon: '/favicon.svg'");
	});
});
