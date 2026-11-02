import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import BuildingIcon from '@lucide/svelte/icons/building-2';
import { firmContext } from './record-focus';
import { buildTenantNav } from './nav';

const ACME = { name: 'Acme Wealth', iconUrl: 'https://cdn.example/acme.png' };

describe('the firm scope header', () => {
	it('names the firm, shows its mark, and carries the way back to the tenant list', () => {
		expect(firmContext(ACME, null)).toEqual({
			eyebrow: 'Firm',
			title: 'Acme Wealth',
			back: { href: '/tenants', label: 'All tenants' },
			image: 'https://cdn.example/acme.png',
			icon: BuildingIcon
		});
	});

	it('falls back to the building glyph for a firm with no icon set', () => {
		const context = firmContext({ name: 'Acme Wealth', iconUrl: null }, null);
		expect(context.image).toBeUndefined();
		expect(context.subtitle).toBeUndefined();
		expect(context.icon).toBe(BuildingIcon);
	});

	it('keeps a focused record’s own header, with the firm in the eyebrow', () => {
		const focus = {
			eyebrow: 'Client',
			title: 'Ada Lovelace',
			subtitle: 'ada@example.com',
			back: { href: '/tenants/ten_acme/operate', label: 'Dashboard' },
			sections: [{ label: 'Overview', href: '/tenants/ten_acme/operate/clients/c1' }]
		};
		expect(firmContext(ACME, focus)).toEqual({
			eyebrow: 'Client · Acme Wealth',
			title: 'Ada Lovelace',
			subtitle: 'ada@example.com',
			back: { href: '/tenants/ten_acme/operate', label: 'Dashboard' }
		});
	});

	it('is the only way back to the tenant list — the nav has no "All tenants" row', () => {
		// Two back links in one column read as two different places to go.
		const labels = buildTenantNav('ten_acme', {
			permissions: ['tenants:read', 'tenant_portal:read'],
			role: null
		}).flatMap((group) => group.items.map((item) => item.label));
		expect(labels).not.toContain('All tenants');
	});

	it('is what the root shell hands the sidebar', () => {
		const layout = readFileSync('src/routes/+layout.svelte', 'utf8');
		expect(layout).toContain('firmContext(firm, focus)');
		expect(layout).toContain('page.data.tenantIconUrl');
		expect(layout).toContain('{context}');
	});
});
