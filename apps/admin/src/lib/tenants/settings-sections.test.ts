import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { settingsSections } from './settings-sections';

const BASE = '/tenants/ten_acme/settings';
const labels = (gates: { canConfigure: boolean; canBrowseStorage: boolean }) =>
	settingsSections(BASE, gates).map((s) => s.label);

describe('the firm Settings page sub-nav', () => {
	it('is General, Sign-in, Platforms, API, Sending, Storage for an operator who holds both gates', () => {
		expect(settingsSections(BASE, { canConfigure: true, canBrowseStorage: true })).toEqual([
			expect.objectContaining({ label: 'General', href: BASE }),
			expect.objectContaining({ label: 'Sign-in', href: `${BASE}/sign-in` }),
			expect.objectContaining({ label: 'Platforms', href: `${BASE}/platforms` }),
			expect.objectContaining({ label: 'API', href: `${BASE}/api` }),
			expect.objectContaining({ label: 'Sending', href: `${BASE}/sending` }),
			expect.objectContaining({ label: 'Storage', href: `${BASE}/storage` })
		]);
	});

	it('offers only what the principal can open', () => {
		expect(labels({ canConfigure: true, canBrowseStorage: false })).toEqual([
			'General',
			'Sign-in',
			'Platforms',
			'API',
			'Sending'
		]);
		expect(labels({ canConfigure: false, canBrowseStorage: true })).toEqual(['Storage']);
		expect(labels({ canConfigure: false, canBrowseStorage: false })).toEqual([]);
	});

	it('points every section at a route that exists', () => {
		for (const section of settingsSections(BASE, { canConfigure: true, canBrowseStorage: true })) {
			const rel = section.href.slice(BASE.length);
			expect(
				existsSync(`src/routes/tenants/[uid]/settings${rel}/+page.svelte`),
				section.label
			).toBe(true);
		}
	});

	it('is what the Settings layout draws, and the sub-pages leave the heading to it', () => {
		const layout = readFileSync('src/routes/tenants/[uid]/settings/+layout.svelte', 'utf8');
		expect(layout).toContain('settingsSections(base');
		expect(layout).toContain('title="Settings"');
		for (const page of ['', '/sign-in', '/platforms', '/api', '/sending', '/storage']) {
			const source = readFileSync(`src/routes/tenants/[uid]/settings${page}/+page.svelte`, 'utf8');
			expect(source, page || '/').not.toContain('<PageHeader');
		}
	});
});
