import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { PLATFORM_NAV_CONCEPTS, PLATFORM_NAV_STRUCTURE_IDS } from '@graphene/ui/nav';
import {
	TENANT_CAPABILITY_MANIFEST,
	capabilityRows,
	tenantCapabilityHref
} from './tenant-capabilities';

describe('tenant capability manifest', () => {
	it('accounts for every closed platform concept exactly once', () => {
		const rows = capabilityRows(PLATFORM_NAV_CONCEPTS);
		expect(rows.map((row) => row.id)).toEqual(PLATFORM_NAV_CONCEPTS.map((row) => row.id));
		expect(Object.keys(TENANT_CAPABILITY_MANIFEST).sort()).toEqual(
			PLATFORM_NAV_CONCEPTS.map((row) => row.id).sort()
		);
		expect(rows.map((row) => row.disposition.label)).toEqual(rows.map((row) => row.label));
	});

	it('maps every live destination to a route that exists today', () => {
		for (const disposition of Object.values(TENANT_CAPABILITY_MANIFEST)) {
			if (disposition.kind === 'personal' || !disposition.available) continue;
			const directory = `src/routes/tenants/[uid]${disposition.path}`;
			expect(
				existsSync(`${directory}/+page.svelte`) || existsSync(`${directory}/+page.server.ts`),
				directory
			).toBe(true);
		}
	});

	it('never emits links for future or personal capabilities', () => {
		for (const disposition of Object.values(TENANT_CAPABILITY_MANIFEST)) {
			if (disposition.kind === 'personal') {
				expect(disposition.reason.length).toBeGreaterThan(20);
			} else if (!disposition.available) {
				expect(disposition.path.startsWith('/operate/')).toBe(true);
			}
		}
	});

	it('leaves only personal capabilities outside tenant operation', () => {
		for (const disposition of Object.values(TENANT_CAPABILITY_MANIFEST)) {
			if (disposition.kind === 'personal') {
				expect('available' in disposition).toBe(false);
			}
		}
	});

	it('keeps every operational concept inside the target-aware Operate route tree', () => {
		for (const [id, disposition] of Object.entries(TENANT_CAPABILITY_MANIFEST)) {
			if (disposition.kind !== 'operate' || !disposition.available) continue;
			expect(disposition.path, id).toMatch(/^\/operate(?:\/|$)/);
			const directory = `src/routes/tenants/[uid]${disposition.path}`;
			const page = readFileSync(`${directory}/+page.svelte`, 'utf8');
			expect(page, id).not.toMatch(/coming soon|not implemented|TODO:/i);
			const server = readFileSync(`${directory}/+page.server.ts`, 'utf8');
			if (id !== 'dashboard') {
				expect(server, id).toMatch(/\$lib\/operate|target(?:Json|Request|Mutation)/);
			}
		}
	});

	it('constructs tenant-uid routes without serial identifiers', () => {
		const clients = TENANT_CAPABILITY_MANIFEST.clients;
		expect(tenantCapabilityHref('ten_acme', clients)).toBe('/tenants/ten_acme/operate/clients');
	});

	it('disposes every concept in PLATFORM_NAV_STRUCTURE as available or explicitly unavailable', () => {
		for (const id of PLATFORM_NAV_STRUCTURE_IDS) {
			const disposition = TENANT_CAPABILITY_MANIFEST[id];
			expect(disposition, id).toBeDefined();
			expect(['operate', 'configure']).toContain(disposition.kind);
			if (disposition.kind === 'operate' || disposition.kind === 'configure') {
				if (!disposition.available) {
					expect(disposition.path.startsWith('/operate/'), id).toBe(true);
				} else {
					const directory = `src/routes/tenants/[uid]${disposition.path}`;
					expect(
						existsSync(`${directory}/+page.svelte`) || existsSync(`${directory}/+page.server.ts`),
						directory
					).toBe(true);
				}
			}
		}
	});
});
