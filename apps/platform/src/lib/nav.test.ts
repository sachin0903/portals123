/**
 * The sidebar IA and — the point of this file — the permission map behind it.
 *
 * A regrouping is a mechanical change that a type-checker cannot police: an item that loses its gate
 * while being moved renders for someone the route then 403s, and one that quietly gains a gate
 * vanishes for someone entitled to it. So the gate of every item is pinned here as a table, asserted
 * in both directions (present with the permission, absent without it), against the inventory the nav
 * had BEFORE the restructure.
 */

import { describe, expect, it } from 'vitest';
import { PERMISSION_SLUGS } from '@graphene/api-client/permissions';
import { buildNav, focusContext, focusNav, focusedNavWithPinned, parseFocus } from './nav';
import {
	clientSidebarFocus,
	PLATFORM_NAV_CONCEPTS,
	PLATFORM_NAV_STRUCTURE
} from '@graphene/ui/nav';

/** Every item, in render order, as "Group / Item". */
const rows = (permissions: string[]): string[] =>
	buildNav({ permissions, role: null }).flatMap((g) =>
		g.items.map((i) => `${g.label ?? '—'} / ${i.label}`)
	);

const labels = (permissions: string[]): string[] =>
	buildNav({ permissions, role: null }).flatMap((g) => g.items.map((i) => i.label));

const hrefOf = (permissions: string[], label: string): string | undefined =>
	buildNav({ permissions, role: null })
		.flatMap((g) => g.items)
		.find((i) => i.label === label)?.href;

/**
 * The gate that reveals each item — the whole security surface of this module.
 * `null` = no gate (visible to any signed-in member of the firm).
 *
 * These are exactly the gates the pre-restructure `+layout.svelte` used. Flows moves from the user
 * popout into the Firm settings group on its unchanged `flows:read` gate; everything else keeps both
 * its gate and its place in the app.
 */
/** A row's gate, or the LIST of gates any one of which reveals it (Templates covers two kinds).
 *  `null` means ungated. */
const GATES: Record<string, string | string[] | null> = {
	Dashboard: null,
	Clients: null,
	Accounts: null,
	Transactions: null,
	Transfers: null,
	Reports: 'reports:read',
	Contributions: 'contributions:read',
	Journeys: 'cases:read',
	Reviews: null,
	Messages: 'messages:read',
	Documents: 'documents:read',
	Team: 'teams:read',
	Profiles: 'profiles:read',
	Roles: 'roles:write',
	Actions: 'actions:read',
	Studio: 'branding:write',
	Flows: 'flows:read',
	// The rule tier sits beside Flows on its OWN gate — arming a rule is not the same grant as
	// authoring a journey (flow-contract.json, $comment_permissions).
	Automations: 'automations:read',
	// TWO GATES, not one. The row covers document AND email templates, so it appears for either —
	// and each is checked on its own below, or a row that only showed when both were held would
	// pass a combined check.
	Templates: ['templates:read', 'email_templates:read'],
	Content: 'content:read',
	Terms: 'terms:read',
	Forms: 'forms:read',
	Types: 'document_types:read',
	Emails: 'activity:read',
	Activity: 'activity:read',
	'Data sources': 'data_sources:read'
};

// Every item inside Firm settings needs `firm_settings:show` IN ADDITION to its own gate above —
// the one explicit group-level gate in nav.ts (see the doc comment on `buildNav`). The generic
// gate-table tests below have to know which labels that applies to, since holding an item's own
// gate alone no longer reveals it.
const FIRM_SETTINGS_SHOW = 'firm_settings:show';
const FIRM_SETTINGS_LABELS = new Set([
	'Team',
	'Profiles',
	'Roles',
	'Actions',
	'Studio',
	'Forms',
	'Flows',
	'Automations',
	'Templates',
	'Content',
	'Terms',
	'Emails',
	'Types',
	'Activity',
	'Data sources'
]);
// The same AND for one row: Documents needs `documents:show` on top of `documents:read`. A role
// with `documents:read` alone reads documents on a client's record but has no firm-wide library.
const DOCUMENTS_SHOW = 'documents:show';
const ROW_SHOW: Record<string, string> = { Documents: DOCUMENTS_SHOW };

/** The permission SET that reveals `label` — its own gate, plus `firm_settings:show` too when
 *  `label` lives inside that group, plus the row's own show gate when it has one. */
const gatePerms = (label: string, gate: string): string[] => [
	...(FIRM_SETTINGS_LABELS.has(label) ? [FIRM_SETTINGS_SHOW] : []),
	...(ROW_SHOW[label] ? [ROW_SHOW[label]] : []),
	gate
];

/** One case per (label, gate) pair, so a row with alternative gates is checked against EACH of them
 *  on its own. Flattened here rather than at every use, because `GATES` now holds a list for the
 *  Templates row and a bare `Object.entries` would hand a test an array where it expects a slug. */
const GATE_CASES: [string, string][] = Object.entries(GATES).flatMap(([label, gate]) =>
	gate === null
		? []
		: (Array.isArray(gate) ? gate : [gate]).map((g) => [label, g] as [string, string])
);

const ALL = [...PERMISSION_SLUGS] as string[];
const UNGATED = Object.entries(GATES)
	.filter(([, gate]) => gate === null)
	.map(([label]) => label);

describe('buildNav — information architecture', () => {
	it('projects group labels and item order from PLATFORM_NAV_STRUCTURE', () => {
		const rendered = buildNav({ permissions: ALL, role: null });
		expect(rendered.map((g) => g.label ?? null)).toEqual(
			PLATFORM_NAV_STRUCTURE.map((g) => g.label ?? null)
		);
		const structureRows = PLATFORM_NAV_STRUCTURE.flatMap((g) =>
			g.items.map((i) => `${g.label ?? '—'} / ${i.id}`)
		);
		const renderedConceptOrder = rendered.flatMap((g) =>
			g.items.map((item) => {
				const concept = PLATFORM_NAV_CONCEPTS.find((c) => c.label === item.label);
				return `${g.label ?? '—'} / ${concept?.id}`;
			})
		);
		expect(renderedConceptOrder).toEqual(structureRows);
	});

	it('renders Dashboard, the three daily-work groups, then Firm settings, for a fully-permissioned adviser', () => {
		expect(rows(ALL)).toEqual([
			'— / Dashboard',
			'Investments / Clients',
			'Investments / Accounts',
			'Investments / Transactions',
			'Investments / Transfers',
			'Investments / Reports',
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
			'Firm settings / Data sources'
		]);
	});

	it('divides Firm settings into the requested subsections', () => {
		const firm = buildNav({ permissions: ALL, role: null }).find(
			(group) => group.label === 'Firm settings'
		);
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
	});

	it('opens the daily-work groups by default and pins Firm settings closed and solo at the bottom', () => {
		const [dashboard, investments, pensions, work, firm] = buildNav({
			permissions: ALL,
			role: null
		});

		// The hub is standalone: no heading, no disclosure that could hide it.
		expect(dashboard.label).toBeUndefined();
		expect(dashboard.collapsible).toBeUndefined();
		expect(dashboard.pinned).toBeUndefined();

		expect(investments).toMatchObject({
			label: 'Investments',
			collapsible: true,
			defaultOpen: true
		});
		expect(investments.pinned).toBeUndefined();
		// Exclusivity is one group's property, not a mode: only Firm settings suppresses the others,
		// so a second `solo` group would be two disclosures fighting over the same column.
		expect([investments.solo, pensions.solo, work.solo, dashboard.solo]).toEqual([
			undefined,
			undefined,
			undefined,
			undefined
		]);

		expect(pensions).toMatchObject({
			label: 'Pensions',
			collapsible: true,
			defaultOpen: true
		});
		expect(pensions.pinned).toBeUndefined();

		expect(work).toMatchObject({
			label: 'Work',
			collapsible: true,
			defaultOpen: true
		});
		expect(work.pinned).toBeUndefined();

		expect(firm).toMatchObject({
			label: 'Firm settings',
			collapsible: true,
			defaultOpen: false,
			pinned: true,
			solo: true
		});
	});

	it('points every item at a real route', () => {
		expect(
			buildNav({ permissions: ALL, role: null }).flatMap((g) => g.items.map((i) => i.href))
		).toEqual([
			'/',
			'/clients',
			'/accounts',
			'/transactions',
			'/transfers',
			'/reports',
			'/contributions',
			'/journeys',
			'/reviews',
			'/chats',
			'/documents',
			'/team',
			'/profiles',
			'/team/roles',
			'/actions',
			'/branding',
			'/forms',
			'/flows',
			'/automations',
			'/templates',
			'/content',
			'/terms',
			'/emails',
			'/documents/types',
			'/activity',
			'/data-sources'
		]);
	});

	it('covers the whole nav in the gate table', () => {
		// Guards the tests below: a new item added without a gate entry would otherwise go unchecked.
		expect(labels(ALL).sort()).toEqual(Object.keys(GATES).sort());
	});

	it('keeps the tenant capability inventory aligned with every sidebar concept', () => {
		expect(
			PLATFORM_NAV_CONCEPTS.filter((concept) => concept.scope === 'tenant')
				.map((concept) => concept.label)
				.sort()
		).toEqual(labels(ALL).sort());
	});
});

describe('buildNav — permission gating', () => {
	it('shows a member with no permissions only the ungated items', () => {
		// Documents/Templates/Forms now carry their own :read gate, so a no-permission member sees
		// only the truly-open book surfaces — and Firm settings (now fully gated) drops out whole.
		// Reviews is open: the role on the action decides who may run or approve it.
		expect(rows([])).toEqual([
			'— / Dashboard',
			'Investments / Clients',
			'Investments / Accounts',
			'Investments / Transactions',
			'Investments / Transfers',
			'Work / Reviews'
		]);
	});

	it.each(GATE_CASES)('%s appears only with %s', (label, gate) => {
		expect(labels([])).not.toContain(label);
		expect(labels(gatePerms(label, gate))).toContain(label);
	});

	it('reveals nothing beyond its own item when a single permission is held (non-Firm-settings items)', () => {
		// The both-directions half of the map: holding one permission must not widen anything beyond
		// the item(s) that permission gates. A gate can front more than one item, so group by gate
		// first (rather than assume a 1:1 gate→label mapping). Firm-settings items are excluded here
		// on purpose — a single gate is never enough for them; see the two tests below.
		const byGate = new Map<string, string[]>();
		for (const [label, gate] of GATE_CASES) {
			if (FIRM_SETTINGS_LABELS.has(label)) continue;
			// A row with its own show gate stays hidden on the item gate alone (the Documents tests below).
			const shown = label in ROW_SHOW ? [] : [label];
			byGate.set(gate, [...(byGate.get(gate) ?? []), ...shown]);
		}
		for (const [gate, gateLabels] of byGate) {
			expect(labels([gate]).sort()).toEqual([...UNGATED, ...gateLabels].sort());
		}
	});

	it('documents:read ALONE hides Documents — documents:show is required too', () => {
		expect(labels(['documents:read'])).not.toContain('Documents');
		expect(labels(['documents:write'])).not.toContain('Documents');
		expect(labels([DOCUMENTS_SHOW])).toEqual(UNGATED);
		expect(labels([DOCUMENTS_SHOW, 'documents:read'])).toContain('Documents');
		// write ⇒ read still holds under the show gate.
		expect(labels([DOCUMENTS_SHOW, 'documents:write'])).toContain('Documents');
	});

	it('a Firm-settings item gate ALONE reveals nothing — firm_settings:show is required too', () => {
		for (const [label, gate] of GATE_CASES) {
			if (!FIRM_SETTINGS_LABELS.has(label)) continue;
			// write ⇒ read still opens a Work item on the same resource. The Firm
			// settings row itself stays hidden.
			const impliedRead = gate.endsWith(':write')
				? gate.slice(0, -':write'.length) + ':read'
				: null;
			const implied = impliedRead
				? GATE_CASES.filter(([l, g]) => g === impliedRead && !FIRM_SETTINGS_LABELS.has(l)).map(
						([l]) => l
					)
				: [];
			expect(labels([gate]).sort()).toEqual([...UNGATED, ...implied].sort());
		}
	});

	it('firm_settings:show ALONE reveals nothing — every item still needs its own gate too', () => {
		expect(labels([FIRM_SETTINGS_SHOW])).toEqual(UNGATED);
		expect(buildNav({ permissions: [FIRM_SETTINGS_SHOW], role: null })).not.toContainEqual(
			expect.objectContaining({ label: 'Firm settings' })
		);
	});

	it('firm_settings:show + the item gate together reveal exactly that item, nothing more', () => {
		const byGate = new Map<string, string[]>();
		for (const [label, gate] of GATE_CASES) {
			if (!FIRM_SETTINGS_LABELS.has(label)) continue;
			byGate.set(gate, [...(byGate.get(gate) ?? []), label]);
		}
		for (const [gate, gateLabels] of byGate) {
			const impliedRead = gate.endsWith(':write')
				? gate.slice(0, -':write'.length) + ':read'
				: null;
			const implied = impliedRead
				? GATE_CASES.filter(([l, g]) => g === impliedRead && !FIRM_SETTINGS_LABELS.has(l)).map(
						([l]) => l
					)
				: [];
			expect(labels([FIRM_SETTINGS_SHOW, gate]).sort()).toEqual(
				[...UNGATED, ...gateLabels, ...implied].sort()
			);
		}
	});

	it('applies write ⇒ read, so a manager sees the read-gated items', () => {
		expect(labels([FIRM_SETTINGS_SHOW, 'teams:write'])).toContain('Team');
		expect(labels([FIRM_SETTINGS_SHOW, 'profiles:write'])).toContain('Profiles');
		expect(labels(['cases:write'])).toContain('Journeys');
		expect(labels(['messages:write'])).toContain('Messages');
		expect(labels([FIRM_SETTINGS_SHOW, 'flows:write'])).toContain('Flows');
	});

	it('does not let a read permission open a write-gated editor', () => {
		// Roles and Studio are the EDITORS; they were gated on :write before the move and still are.
		// firm_settings:show is included so the negative is specifically about read vs write, not
		// just the group-level gate also being absent.
		expect(labels([FIRM_SETTINGS_SHOW, 'roles:read'])).not.toContain('Roles');
		expect(labels([FIRM_SETTINGS_SHOW, 'branding:read'])).not.toContain('Studio');
	});

	it('shows everything to the system superuser', () => {
		expect(labels(['system']).sort()).toEqual(Object.keys(GATES).sort());
	});

	it('never trusts the role name — a firm cannot name a role its way past a gate', () => {
		// `can` deliberately ignores `role` (a tenant names its own roles in its own WorkOS project).
		expect(
			buildNav({ permissions: [], role: 'admin' }).flatMap((g) => g.items.map((i) => i.label))
		).toEqual(UNGATED);
	});

	it('never renders an empty group heading', () => {
		// Pensions and Firm settings are fully gated, so a member must not see a bare heading.
		// Work stays, because Reviews is open to any staff member.
		// The filter that drops empty groups is the live branch this pins — including the case where
		// firm_settings:show is held but no individual item's own gate is (Firm settings itself
		// must drop out, not render as an empty heading).
		for (const permissions of [[], [FIRM_SETTINGS_SHOW], ['members:read'], ALL]) {
			for (const group of buildNav({ permissions, role: null })) {
				expect(group.items.length).toBeGreaterThan(0);
			}
		}
		expect(buildNav({ permissions: [], role: null }).map((g) => g.label ?? '—')).toEqual([
			'—',
			'Investments',
			'Work'
		]);
	});

	it('keeps Roles pointing at the editor under Team, and Team matching exactly', () => {
		// Roles is a CHILD path of Team, so Team has to opt out of prefix matching or `/team/roles`
		// lights up both rows — which reads as a broken active indicator, not as a hierarchy.
		expect(hrefOf(ALL, 'Roles')).toBe('/team/roles');
		const items = buildNav({ permissions: ALL, role: null }).flatMap((g) => g.items);
		expect(items.find((i) => i.label === 'Team')?.exact).toBe(true);
		// Nothing else needs it: no other item is a prefix of a sibling's href.
		expect(items.filter((i) => i.exact).map((i) => i.label)).toEqual(['Team']);
	});

	it('carries the unread dot on Messages from the layout poll', () => {
		const unread = buildNav({ permissions: ALL, role: null, chatsUnread: true })
			.flatMap((g) => g.items)
			.find((i) => i.label === 'Messages');
		const read = buildNav({ permissions: ALL, role: null, chatsUnread: false })
			.flatMap((g) => g.items)
			.find((i) => i.label === 'Messages');
		expect(unread?.unread).toBe(true);
		expect(read?.unread).toBe(false);
	});
});

describe('sidebar focus', () => {
	const focus = {
		eyebrow: 'CLIENT',
		title: 'Mr Max Oakden',
		subtitle: 'max.oakden@example.com',
		back: { href: '/', label: 'Dashboard' },
		sections: [
			{ label: 'Overview', href: '/clients/12', icon: 'overview', exact: true },
			{ label: 'Documents', href: '/clients/12/documents', icon: 'documents' },
			{ label: 'Notes', href: '/clients/12/notes', icon: 'nope' }
		]
	};

	it('is absent unless a page publishes one', () => {
		expect(parseFocus(undefined)).toBeNull();
		expect(parseFocus(null)).toBeNull();
		expect(parseFocus('client')).toBeNull();
		expect(parseFocus({ sections: [] })).toBeNull(); // no title ⇒ nothing to focus on
		expect(parseFocus({ title: '   ' })).toBeNull();
	});

	it('reads a published focus', () => {
		expect(parseFocus(focus)).toEqual(focus);
	});

	it('drops half-formed sections instead of rendering blank rows', () => {
		const parsed = parseFocus({
			title: 'Acme Ltd',
			sections: [{ label: 'Overview' }, { href: '/clients/1/notes' }, 'nope', null]
		});
		expect(parsed).toEqual({ title: 'Acme Ltd' });
		expect(focusNav(parsed!)).toEqual([]);
	});

	it('carries liveUnreadContactId through, and drops it when absent or blank', () => {
		expect(parseFocus({ title: 'Acme Ltd', liveUnreadContactId: '42' })?.liveUnreadContactId).toBe(
			'42'
		);
		expect(parseFocus({ title: 'Acme Ltd' })?.liveUnreadContactId).toBeUndefined();
		expect(
			parseFocus({ title: 'Acme Ltd', liveUnreadContactId: '  ' })?.liveUnreadContactId
		).toBeUndefined();
	});

	it("carries a section's unread dot through to the built nav item, and drops it when absent", () => {
		const parsed = parseFocus({
			title: 'Mr Max Oakden',
			sections: [
				{ label: 'Overview', href: '/clients/12', unread: true },
				{ label: 'Documents', href: '/clients/12/documents' }
			]
		});
		const [group] = focusNav(parsed!);
		expect(group.items.find((i) => i.label === 'Overview')?.unread).toBe(true);
		expect(group.items.find((i) => i.label === 'Documents')?.unread).toBeUndefined();
	});

	it('ignores a back link that is missing half of itself', () => {
		expect(parseFocus({ title: 'Acme Ltd', back: { href: '/clients' } })).toEqual({
			title: 'Acme Ltd'
		});
	});

	it('builds one unlabelled group and resolves icons by name', () => {
		const [group, ...rest] = focusNav(parseFocus(focus)!);
		expect(rest).toEqual([]);
		expect(group.label).toBeUndefined();
		expect(group.items.map((i) => i.label)).toEqual(['Overview', 'Documents', 'Notes']);
		expect(group.items[0].exact).toBe(true);
		expect(group.items[0].icon).toBeTruthy();
		expect(group.items[1].icon).toBeTruthy();
		// An icon key the chrome doesn't know renders no icon rather than throwing.
		expect(group.items[2].icon).toBeUndefined();
		expect(group.items[1].exact).toBeUndefined();
	});

	it('resolves an icon for every section the client record actually publishes', () => {
		// The one silent failure this contract can produce: a producer names an icon key the chrome's
		// registry doesn't have, and that row renders label-only — no error, just an odd gap. Checked
		// against the real producer rather than a fixture.
		const published = clientSidebarFocus({
			uid: 'con_12ab',
			name: 'Mr Max Oakden',
			email: 'max.oakden@example.com',
			canReadDocuments: true
		});
		const parsed = parseFocus(published);
		expect(parsed).not.toBeNull();
		expect(parsed!.sections?.length).toBe(published.sections?.length);
		const [group] = focusNav(parsed!);
		expect(group.items.filter((i) => !i.icon).map((i) => i.label)).toEqual([]);
	});

	it('hands the shell only the context header fields', () => {
		expect(focusContext(parseFocus(focus)!)).toEqual({
			eyebrow: 'CLIENT',
			title: 'Mr Max Oakden',
			subtitle: 'max.oakden@example.com',
			back: { href: '/', label: 'Dashboard' }
		});
	});
});

/**
 * Firm settings has to survive a focused record.
 *
 * THE DEFECT. A focused sidebar replaced the global one outright, so opening a client or an account
 * took Firm settings with it — and the way to reach Team, Roles, Templates or the Studio became
 * "navigate out of the record first". Firm settings is where you go to change how the thing you are
 * looking at behaves; being unable to reach it from inside a record is backwards.
 */
describe('a focused record keeps the pinned groups', () => {
	const nav = buildNav({ permissions: [...PERMISSION_SLUGS], role: null });
	const focus = clientSidebarFocus({
		uid: 'c_1',
		name: 'Simple Accumulation',
		canReadMessages: true
	} as Parameters<typeof clientSidebarFocus>[0]);

	it('renders the record’s own sections FIRST', () => {
		const out = focusedNavWithPinned(focus, nav);
		expect(out.length).toBeGreaterThan(1);
		expect(out[0].items.length).toBeGreaterThan(0);
		expect(out[0].pinned).not.toBe(true);
	});

	it('keeps Firm settings reachable', () => {
		const out = focusedNavWithPinned(focus, nav);
		const labels = out.flatMap((g) => g.items.map((i) => i.label));
		expect(labels).toContain('Team');
		expect(labels).toContain('Roles');
	});

	it('brings across the PINNED groups only — the daily-work ones stay behind', () => {
		// A record's sections ARE the navigation while you are in it; re-listing Clients and
		// Accounts above them would put two competing navigations in one sidebar.
		const out = focusedNavWithPinned(focus, nav);
		const labels = out.flatMap((g) => g.items.map((i) => i.label));
		expect(labels).not.toContain('Clients');
		expect(labels).not.toContain('Accounts');
		expect(out.slice(1).every((g) => g.pinned === true)).toBe(true);
	});

	it('falls back to the global nav when nothing is focused', () => {
		expect(focusedNavWithPinned(null, nav)).toEqual([]);
	});

	it('falls back when a focus carries a title but no sections', () => {
		// Appending the pinned groups to nothing would strand them as the entire sidebar.
		expect(focusedNavWithPinned({ title: 'Some record', sections: [] }, nav)).toEqual([]);
	});
});

describe('the dashboard row', () => {
	it('is the source of the name "Dashboard"', () => {
		// `sectionBreadcrumb('/')` returns null (see breadcrumb.test.ts), so the root layout names the
		// dashboard from the nav — which is what makes a task opened from it read "Back to Dashboard".
		// The pair is the contract; neither file owns both halves.
		const items = buildNav({ permissions: [], role: null }).flatMap((g) => g.items);
		expect(items.find((i) => i.href === '/')).toMatchObject({ label: 'Dashboard', href: '/' });
	});
});
