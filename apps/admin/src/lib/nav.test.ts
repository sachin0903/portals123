/**
 * The Internal Operations sidebar IA and — the point of this file — the permission map behind it.
 *
 * A regrouping is a mechanical change a type-checker cannot police: an item that loses its gate
 * while being moved renders for an operator the route then 403s, and one that quietly gains a gate
 * vanishes for somebody entitled to it. So the gate of every item is pinned here as a table,
 * asserted in both directions (present with the permission, absent without it), against the
 * inventory the flat nav had BEFORE the restructure.
 *
 * It matters more on this surface than in the platform portal: admin's gates partition the operator
 * population rather than shading one adviser's view, so a wrong gate hands one job's console to
 * another job.
 */

import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { PERMISSION_SLUGS } from '@graphene/api-client/permissions';
import { buildGlobalNav, buildTenantNav } from '$lib/nav';
import { TENANT_CAPABILITY_MANIFEST } from '$lib/tenant-capabilities';
import { PLATFORM_NAV_STRUCTURE } from '@graphene/ui/nav';

/** Every item, in render order, as "Group / Item". */
const rows = (permissions: string[]): string[] =>
	buildGlobalNav({ permissions, role: null }).flatMap((g) =>
		g.items.map((i) => `${g.label ?? '—'} / ${i.label}`)
	);

const labels = (permissions: string[]): string[] =>
	buildGlobalNav({ permissions, role: null }).flatMap((g) => g.items.map((i) => i.label));

const items = (permissions: string[]) =>
	buildGlobalNav({ permissions, role: null }).flatMap((g) => g.items);

/**
 * The gate that reveals each item — the whole security surface of this module.
 * `null` = no gate (visible to any signed-in operator). Nothing in this nav is ungated.
 */
const GATES: Record<string, string | null> = {
	Dashboard: 'tenants:read',
	Tenants: 'tenants:read',
	Users: 'tenants:read',
	'Model tenant': 'model:read',
	Platforms: 'platforms:read',
	'API log': 'platforms:read',
	'Look-through': 'lookthrough:read',
	Illustrations: 'platforms:read',
	Integrations: 'integrations:read',
	// The same read permissions the platform workbenches enforce. The pages are prototypes, and
	// the loaders refuse anyone without the slug.
	Contributions: 'contributions:read',
	Transfers: 'transfers:read',
	AML: 'aml:read',
	Activity: 'audit:read',
	'Audit log': 'audit:read',
	Staff: 'operations:read',
	Roles: 'operations:read',
	Terms: 'platform_terms:read',
	Reports: 'mi_reports:read',
	// Granted independently of `tenants:read` on purpose, which is why the kill switch is its own row
	// rather than a tile on the dashboard.
	Automations: 'automations:global_pause'
};

const ALL = [...PERMISSION_SLUGS] as string[];
const UNGATED = Object.entries(GATES)
	.filter(([, gate]) => gate === null)
	.map(([label]) => label);

describe('buildGlobalNav — information architecture', () => {
	it('renders Dashboard, the four subject groups, then Internal operations, for a superuser', () => {
		expect(rows(ALL)).toEqual([
			'— / Dashboard',
			'Firms / Tenants',
			'Firms / Users',
			'Firms / Model tenant',
			'Data / Platforms',
			'Data / API log',
			'Data / Look-through',
			'Data / Illustrations',
			'Data / Integrations',
			'Money / Contributions',
			'Money / Transfers',
			'Oversight / AML',
			'Oversight / Activity',
			'Oversight / Audit log',
			'Internal operations / Staff',
			'Internal operations / Roles',
			'Internal operations / Terms',
			'Internal operations / Reports',
			'Internal operations / Automations'
		]);
	});

	it('points every item at a real route', () => {
		expect(items(ALL).map((i) => i.href)).toEqual([
			'/',
			'/tenants',
			'/users',
			'/model',
			'/platforms',
			'/platforms/api',
			'/look-through',
			'/illustrations',
			'/integrations',
			'/contributions',
			'/transfers',
			'/aml',
			'/activity',
			'/audit',
			'/operations/users',
			'/operations/roles',
			'/operations/terms',
			'/operations/reports',
			'/automations'
		]);
	});

	it('opens every group and pins only Internal operations, without going solo', () => {
		const [dashboard, firms, data, money, oversight, internal] = buildGlobalNav({
			permissions: ALL,
			role: null
		});

		// The hub is standalone: no heading, no disclosure that could hide it.
		expect(dashboard.label).toBeUndefined();
		expect(dashboard.collapsible).toBeUndefined();
		expect(dashboard.pinned).toBeUndefined();

		for (const group of [firms, data, money, oversight]) {
			expect(group).toMatchObject({ collapsible: true, defaultOpen: true });
			expect(group.pinned).toBeUndefined();
		}
		expect([firms.label, data.label, money.label, oversight.label]).toEqual([
			'Firms',
			'Data',
			'Money',
			'Oversight'
		]);

		// Pinned but OPEN, unlike the platform's Firm settings: five rows do not crowd a viewport,
		// and closing it would put the estate-wide kill switch behind a disclosure.
		expect(internal).toMatchObject({
			label: 'Internal operations',
			collapsible: true,
			defaultOpen: true,
			pinned: true
		});

		// `solo` is for a long pinned group holding a short column hostage (the platform's fourteen
		// rows). Nothing here earns it, and a second solo group would be two disclosures fighting.
		expect([dashboard, firms, data, money, oversight, internal].map((g) => g.solo)).not.toContain(
			true
		);
	});

	it('names no group after the portal or after its own first row', () => {
		// An earlier cut had a group called "Operations" inside the Internal Operations portal, and a
		// group called "Platforms" whose first row was also "Platforms". Both read as generated chrome.
		const groups = buildGlobalNav({ permissions: ALL, role: null });
		for (const group of groups) {
			if (!group.label) continue;
			expect(group.label).not.toBe('Operations');
			expect(group.items.map((i) => i.label)).not.toContain(group.label);
		}
	});

	it('gives every row its own glyph', () => {
		// Two identical icons in one sidebar put two meanings on one symbol, and the flat nav had
		// three such pairs: `scroll-text` on API log and Audit log, `shield-check` on Model tenant and
		// Roles, and `users` on both Users and Staff — two lists of people whose only distinguishing
		// mark was the glyph they shared. They took `webhook`, `copy` and `user-cog`. This is the
		// regression that would otherwise pass every other test in the file.
		const icons = items(ALL).map((i) => i.icon);
		expect(icons.filter((icon) => icon === undefined)).toEqual([]);
		expect(new Set(icons).size).toBe(icons.length);
	});

	it('covers the whole nav in the gate table', () => {
		// Guards the tests below: a new item added without a gate entry would otherwise go unchecked.
		expect(labels(ALL).sort()).toEqual(Object.keys(GATES).sort());
	});
});

describe('buildGlobalNav — permission gating', () => {
	it('shows an operator with no permissions nothing', () => {
		expect(rows([])).toEqual([]);
	});

	it.each(Object.entries(GATES).filter(([, gate]) => gate !== null) as [string, string][])(
		'%s appears only with %s',
		(label, gate) => {
			expect(labels([])).not.toContain(label);
			expect(labels([gate])).toContain(label);
		}
	);

	it('reveals nothing beyond its own items when a single permission is held', () => {
		// The both-directions half of the map: holding one permission must not widen anything beyond
		// the item(s) that permission gates. A gate can front more than one row (tenants:read fronts
		// three), so group by gate first rather than assume a 1:1 gate→label mapping.
		const byGate = new Map<string, string[]>();
		for (const [label, gate] of Object.entries(GATES)) {
			if (!gate) continue;
			byGate.set(gate, [...(byGate.get(gate) ?? []), label]);
		}
		for (const [gate, gateLabels] of byGate) {
			expect(labels([gate]).sort()).toEqual([...UNGATED, ...gateLabels].sort());
		}
	});

	it('applies write ⇒ read, so a manager sees the read-gated sections', () => {
		expect(labels(['tenants:write'])).toContain('Tenants');
		expect(labels(['model:write'])).toContain('Model tenant');
		expect(labels(['platforms:write'])).toContain('Platforms');
		expect(labels(['lookthrough:write'])).toContain('Look-through');
		expect(labels(['integrations:write'])).toContain('Integrations');
		expect(labels(['aml:write'])).toContain('AML');
		expect(labels(['operations:write'])).toContain('Staff');
		expect(labels(['contributions:write'])).toContain('Contributions');
		expect(labels(['transfers:write'])).toContain('Transfers');
	});

	it('does not let the firm-side automations permissions open the estate kill switch', () => {
		// `automations:global_pause` (operations) and `automations:pause` (a firm pausing its own) are
		// different grants that read almost identically. Neither firm-side slug may reveal this row.
		expect(labels(['automations:pause'])).not.toContain('Automations');
		expect(labels(['automations:write'])).not.toContain('Automations');
	});

	it('shows everything to the system superuser', () => {
		expect(labels(['system']).sort()).toEqual(Object.keys(GATES).sort());
	});

	it('never trusts the role name — a firm cannot name a role its way past a gate', () => {
		// `can` deliberately ignores `role` (a tenant names its own roles in its own WorkOS project),
		// which on this surface would be a cross-tenant superuser bypass.
		expect(labels([])).toEqual(UNGATED);
		expect(
			buildGlobalNav({ permissions: [], role: 'admin' }).flatMap((g) => g.items.map((i) => i.label))
		).toEqual(UNGATED);
	});

	it('never renders an empty group heading', () => {
		// Every group is fully gated, so a narrow operator must not see a bare heading.
		// The filter that drops empty groups is the live branch this pins.
		for (const permissions of [[], ['tenants:read'], ['aml:read'], ALL]) {
			for (const group of buildGlobalNav({ permissions, role: null })) {
				expect(group.items.length).toBeGreaterThan(0);
			}
		}
		expect(buildGlobalNav({ permissions: [], role: null }).map((g) => g.label ?? '—')).toEqual([]);
		// A pipeline operator gets Data and nothing else — no Dashboard, since that is `tenants:read`.
		expect(rows(['platforms:read'])).toEqual([
			'Data / Platforms',
			'Data / API log',
			'Data / Illustrations'
		]);
	});

	it('keeps Platforms matching exactly, since the API log is a child path', () => {
		// `/platforms/api` is a CHILD of `/platforms`, so Platforms has to opt out of prefix matching
		// or that URL lights up both rows — which reads as a broken active indicator, not a hierarchy.
		expect(items(ALL).find((i) => i.label === 'Platforms')?.exact).toBe(true);
		// Nothing else needs it: no other item's href is a prefix of a sibling's.
		expect(
			items(ALL)
				.filter((i) => i.exact)
				.map((i) => i.label)
		).toEqual(['Platforms']);
	});
});

describe('buildTenantNav — operating a firm, and configuring it', () => {
	const tenantGroups = (permissions: string[]) =>
		buildTenantNav('ten_acme', { permissions, role: null });

	const tenantRows = (permissions: string[]) =>
		tenantGroups(permissions).flatMap((group) =>
			group.items.map((item) => `${group.label ?? '—'} / ${item.label}`)
		);

	const tenantItems = (permissions: string[]) =>
		tenantGroups(permissions).flatMap((group) => group.items);

	/**
	 * The gate of every row in the firm sidebar. `configure` stands for the either-or the whole
	 * configuration surface shares (`tenants:read` OR `model:read` — the model tenant is
	 * configured through this same sidebar).
	 */
	const TENANT_GATES: Record<string, string | null> = {
		Overview: 'configure',
		Dashboard: 'tenant_portal:read',
		Clients: 'tenant_portal:read',
		Accounts: 'tenant_portal:read',
		Transactions: 'tenant_portal:read',
		Transfers: 'tenant_portal:read',
		Contributions: 'tenant_portal:read',
		Journeys: 'tenant_portal:read',
		Reviews: 'tenant_portal:read',
		Messages: 'tenant_portal:read',
		Documents: 'tenant_portal:read',
		Team: 'configure',
		Profiles: 'configure',
		Roles: 'configure',
		Actions: 'tenant_portal:read',
		Studio: 'configure',
		Forms: 'tenant_portal:read',
		Flows: 'tenant_portal:read',
		Automations: 'tenant_portal:read',
		Templates: 'tenant_portal:read',
		Content: 'tenant_portal:read',
		Terms: 'tenant_portal:read',
		Emails: 'tenant_portal:read',
		Types: 'tenant_portal:read',
		// The only conjunction in the sidebar: it is an operating row (so the workspace permission
		// opens it) AND its own loader enforces `audit:read`, so neither slug alone reveals it.
		Activity: 'tenant_portal:read+audit:read',
		'Data sources': 'tenant_portal:read',
		Settings: 'configure',
		Products: 'platforms:read',
		Models: 'platforms:read',
		Instruments: 'platforms:read',
		'Fee packages': 'platforms:read',
		Illustrations: 'platforms:read',
		'Transfer providers': 'platforms:read',
		'Third-party providers': 'platforms:read',
		Terminology: 'platforms:read',
		'Deleted documents': 'book:read',
		'Audit log': 'audit:read'
	};

	it('mirrors the platform IA, then appends Configure', () => {
		expect(tenantGroups(ALL).map((group) => group.label ?? '—')).toEqual([
			'—',
			'Investments',
			'Pensions',
			'Work',
			'Firm settings',
			'Configure'
		]);
		expect(tenantRows(ALL)).toEqual([
			'— / Overview',
			'— / Dashboard',
			'Investments / Clients',
			'Investments / Accounts',
			'Investments / Transactions',
			'Investments / Transfers',
			'Pensions / Contributions',
			'Work / Journeys',
			'Work / Reviews',
			'Work / Messages',
			'Work / Documents',
			'Firm settings / Team',
			'Firm settings / Profiles',
			'Firm settings / Roles',
			'Firm settings / Actions',
			'Firm settings / Studio',
			'Firm settings / Forms',
			'Firm settings / Flows',
			'Firm settings / Automations',
			'Firm settings / Templates',
			'Firm settings / Content',
			'Firm settings / Terms',
			'Firm settings / Emails',
			'Firm settings / Types',
			'Firm settings / Activity',
			'Firm settings / Data sources',
			'Configure / Settings',
			'Configure / Products',
			'Configure / Models',
			'Configure / Instruments',
			'Configure / Fee packages',
			'Configure / Illustrations',
			'Configure / Transfer providers',
			'Configure / Third-party providers',
			'Configure / Terminology',
			'Configure / Deleted documents',
			'Configure / Audit log'
		]);
	});

	it('projects mirror groups from PLATFORM_NAV_STRUCTURE', () => {
		const mirror = tenantGroups(ALL).filter((g) => g.label !== 'Configure');
		// The hub row is admin-only; the remaining labelled groups match the structure.
		expect(mirror.map((g) => g.label ?? null).filter(Boolean)).toEqual(
			PLATFORM_NAV_STRUCTURE.filter((g) => g.label).map((g) => g.label)
		);
	});

	it('keeps the daily groups open and pins Firm settings closed and solo', () => {
		const groups = tenantGroups(ALL);
		const byLabel = new Map(groups.map((group) => [group.label, group]));
		for (const label of ['Investments', 'Pensions', 'Work']) {
			expect(byLabel.get(label)).toMatchObject({ collapsible: true, defaultOpen: true });
			expect(byLabel.get(label)?.pinned).toBeUndefined();
		}
		expect(byLabel.get('Firm settings')).toMatchObject({
			collapsible: true,
			defaultOpen: false,
			pinned: true,
			solo: true
		});
		expect(byLabel.get('Configure')).toMatchObject({
			collapsible: true,
			defaultOpen: false,
			pinned: true,
			solo: true
		});
		// The two long pinned groups, and nothing else. Opening one closes the other.
		expect(groups.filter((group) => group.solo).map((group) => group.label)).toEqual([
			'Firm settings',
			'Configure'
		]);
	});

	it('subsections Firm settings like the platform, and Configure for Graphene-only rows', () => {
		const firm = tenantGroups(ALL).find((group) => group.label === 'Firm settings');
		expect(firm?.items.map(({ section, label }) => `${section} / ${label}`)).toEqual([
			'People & Access / Team',
			'People & Access / Profiles',
			'People & Access / Roles',
			'People & Access / Actions',
			'Platform Builder / Studio',
			'Platform Builder / Forms',
			'Platform Builder / Flows',
			'Platform Builder / Automations',
			'Communications / Templates',
			'Communications / Content',
			'Communications / Terms',
			'Communications / Emails',
			'Communications / Types',
			'Data / Activity',
			'Data / Data sources'
		]);
		const configure = tenantGroups(ALL).find((group) => group.label === 'Configure');
		// Settings, the Catalogue, Oversight — and nothing the firm already reaches from Firm settings.
		expect([...new Set(configure?.items.map((item) => item.section))]).toEqual([
			undefined,
			'Catalogue',
			'Oversight'
		]);
		const narrow = tenantGroups(['platforms:read']).find((group) => group.label === 'Configure');
		expect([...new Set(narrow?.items.map((item) => item.section))]).toEqual(['Catalogue']);
	});

	it('gives both hubs their own row, and matches them exactly', () => {
		const [hubs] = tenantGroups(ALL);
		// No "All tenants" row: the sidebar's scope header carries the way back (record-focus.test).
		expect(tenantItems(ALL).map((item) => item.label)).not.toContain('All tenants');
		expect(hubs.items).toEqual([
			expect.objectContaining({ label: 'Overview', href: '/tenants/ten_acme', exact: true }),
			expect.objectContaining({
				label: 'Dashboard',
				href: '/tenants/ten_acme/operate',
				exact: true
			})
		]);
		expect(
			tenantItems(ALL)
				.filter((item) => item.exact)
				.map((item) => item.label)
		).toEqual(['Overview', 'Dashboard', 'Studio']);
	});

	it('addresses every operating row through the capability manifest', () => {
		const operating = new Set([
			'Clients',
			'Accounts',
			'Transactions',
			'Transfers',
			'Contributions',
			'Journeys',
			'Reviews',
			'Messages',
			'Documents',
			'Actions',
			'Forms',
			'Flows',
			'Automations',
			'Templates',
			'Content',
			'Terms',
			'Types',
			'Data sources',
			'Emails',
			'Activity',
			'Dashboard'
		]);
		const paths = new Set(
			Object.values(TENANT_CAPABILITY_MANIFEST).flatMap((capability) =>
				capability.kind === 'operate' && capability.available
					? [`/tenants/ten_acme${capability.path}`]
					: []
			)
		);
		for (const item of tenantItems(ALL).filter((item) => operating.has(item.label))) {
			expect(paths).toContain(item.href);
		}
	});

	it('covers the whole firm sidebar in the gate table', () => {
		expect(
			tenantItems(ALL)
				.map((item) => item.label)
				.sort()
		).toEqual(Object.keys(TENANT_GATES).sort());
	});

	it.each(Object.entries(TENANT_GATES).filter(([, gate]) => gate !== null) as [string, string][])(
		'%s appears only with %s',
		(label, gate) => {
			const labelsFor = (permissions: string[]) =>
				tenantItems(permissions).map((item) => item.label);
			expect(labelsFor([])).not.toContain(label);
			if (gate === 'configure') {
				expect(labelsFor(['tenants:read'])).toContain(label);
				expect(labelsFor(['model:read'])).toContain(label);
				expect(labelsFor(['tenant_portal:write'])).not.toContain(label);
				return;
			}
			const slugs = gate.split('+');
			expect(labelsFor(slugs)).toContain(label);
			for (const slug of slugs.length > 1 ? slugs : []) {
				expect(labelsFor([slug])).not.toContain(label);
			}
		}
	);

	it('does not make the book a second gate on the workspace’s own tabs', () => {
		const rows = tenantRows(['tenant_portal:read']);
		expect(rows).toContain('Investments / Clients');
		expect(rows).toContain('Investments / Accounts');
		expect(rows).toContain('Investments / Transactions');
		expect(rows).not.toContain('Configure / Settings');
		expect(rows).not.toContain('Configure / Deleted documents');
	});

	it('offers Settings to a book reader too, for its Storage section', () => {
		// Storage moved from its own row into the Settings page. Its gate is unchanged (book:read,
		// enforced by its loader), so a book reader must still be able to reach it — via Settings.
		expect(tenantRows(['book:read'])).toContain('Configure / Settings');
		expect(tenantRows(['tenant_portal:read'])).not.toContain('Configure / Settings');
	});

	it('does not repeat what Firm settings already reaches', () => {
		// Notification rules are configured from Activity (the platform's own route to them);
		// sign-in, platforms and storage are sections of Settings, not rows.
		const labels = tenantItems(ALL).map((item) => item.label);
		for (const gone of ['Notifications', 'WorkOS config', 'Platforms', 'Storage', 'Sign-in']) {
			expect(labels, gone).not.toContain(gone);
		}
	});

	it('accepts write through write-implies-read, and shows nothing else', () => {
		const write = tenantRows(['tenant_portal:write']);
		expect(write).toContain('Work / Journeys');
		expect(write).not.toContain('— / Overview');
		expect(write).not.toContain('Firm settings / Activity');
	});

	it('gives every row in one firm sidebar its own glyph', () => {
		const items = tenantItems(ALL);
		const icons = items.map((item) => item.icon);
		expect(icons.filter((icon) => icon === undefined)).toEqual([]);
		expect(new Set(icons).size).toBe(icons.length);
	});

	it('does not trust an operations-looking role name', () => {
		// Nothing at all: without a permission there is no row to show, and the way back to the
		// tenant list lives in the scope header, not here.
		expect(buildTenantNav('ten_acme', { permissions: [], role: 'system' })).toEqual([]);
	});

	it('never renders an empty group heading', () => {
		for (const permissions of [[], ['tenants:read'], ['tenant_portal:read'], ['audit:read'], ALL]) {
			for (const group of tenantGroups(permissions)) {
				expect(group.items.length).toBeGreaterThan(0);
			}
		}
	});

	it('is the tenant navigation source used by the root shell', () => {
		const layout = readFileSync('src/routes/+layout.svelte', 'utf8');
		expect(layout).toContain('buildTenantNav(tenantUid, { permissions, role })');
		expect(layout).not.toContain("label: tenantName ?? 'Tenant'");
	});
});
