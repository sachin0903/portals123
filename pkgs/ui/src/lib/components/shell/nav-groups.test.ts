import { afterEach, describe, expect, it } from 'vitest';
import {
	applyGroupOpen,
	groupHoldsHref,
	groupKey,
	initials,
	isGroupOpen,
	navStateKey,
	parseNavState,
	personInitials,
	railRuleKeys,
	readNavState,
	soloGroupOpen,
	splitNav
} from './nav-groups.js';
import type { NavGroup } from './types.js';

const group = (over: Partial<NavGroup> = {}): NavGroup => ({
	items: [{ label: 'Clients', href: '/clients' }],
	...over
});

describe('groupKey', () => {
	it('prefers the label', () => {
		expect(groupKey(group({ label: 'Operations' }), 0)).toBe('Operations');
	});

	it('falls back to the first item href for an unlabelled group', () => {
		// Unlabelled groups are keyed by content, not position: a permission-gated group that
		// disappears must not hand its index — and so its persisted open state and its DOM — to the
		// next group along.
		expect(groupKey(group(), 3)).toBe('/clients');
	});

	it('still yields a key for an empty unlabelled group', () => {
		expect(groupKey({ items: [] }, 2)).toBe('group-2');
	});
});

describe('splitNav', () => {
	it('separates pinned groups and keeps declared order within each half', () => {
		const ops = group({ label: 'Operations' });
		const firm = group({ label: 'Firm', pinned: true });
		const dash = group({ label: 'Dashboard' });
		const { main, pinned } = splitNav([ops, firm, dash]);
		expect(main.map((g) => g.label)).toEqual(['Operations', 'Dashboard']);
		expect(pinned.map((g) => g.label)).toEqual(['Firm']);
	});

	it('handles a nav with no pinned groups', () => {
		expect(splitNav([group()]).pinned).toEqual([]);
	});
});

describe('navStateKey', () => {
	it('namespaces per portal, slug-cased', () => {
		expect(navStateKey('Platform')).toBe('gp-nav-groups:platform');
		expect(navStateKey('Internal Operations')).toBe('gp-nav-groups:internal-operations');
	});
});

describe('parseNavState', () => {
	it('reads a boolean map', () => {
		expect(parseNavState('{"Firm":false,"Operations":true}')).toEqual({
			Firm: false,
			Operations: true
		});
	});

	it('degrades to no-preference on anything unusable', () => {
		// localStorage is user-writable and survives releases. A throw here happens during sidebar
		// init, which takes down the whole shell — every one of these must return {} instead.
		for (const raw of [null, undefined, '', 'not json', '[1,2]', 'null', '"a string"', '42']) {
			expect(parseNavState(raw), String(raw)).toEqual({});
		}
	});

	it('drops non-boolean entries but keeps the valid ones', () => {
		expect(parseNavState('{"Firm":"false","Ops":true,"Other":null}')).toEqual({ Ops: true });
	});
});

describe('readNavState', () => {
	// vitest runs these in node, with no `localStorage` — which is exactly the SSR case.
	afterEach(() => {
		Reflect.deleteProperty(globalThis, 'localStorage');
	});

	it('returns no preference when there is no localStorage (SSR)', () => {
		expect(readNavState('Platform')).toEqual({});
	});

	it('reads the portal-scoped key', () => {
		const store: Record<string, string> = {
			'gp-nav-groups:platform': '{"Firm":false}',
			'gp-nav-groups:admin': '{"Firm":true}'
		};
		Object.defineProperty(globalThis, 'localStorage', {
			value: { getItem: (k: string) => store[k] ?? null },
			configurable: true
		});
		expect(readNavState('Platform')).toEqual({ Firm: false });
		expect(readNavState('Admin')).toEqual({ Firm: true });
	});

	it('survives storage being blocked outright', () => {
		// Safari with cookies blocked throws on ACCESS, not just on write. This runs during sidebar
		// init, so an uncaught throw here is a blank app, not a lost preference.
		Object.defineProperty(globalThis, 'localStorage', {
			get() {
				throw new DOMException('SecurityError');
			},
			configurable: true
		});
		expect(readNavState('Platform')).toEqual({});
	});
});

describe('isGroupOpen', () => {
	it('is always open when the group is not collapsible', () => {
		expect(isGroupOpen(group(), { X: false }, 'X')).toBe(true);
	});

	it('defaults a collapsible group to open', () => {
		expect(isGroupOpen(group({ collapsible: true }), {}, 'Ops')).toBe(true);
	});

	it('honours defaultOpen: false until the user says otherwise', () => {
		const g = group({ collapsible: true, defaultOpen: false });
		expect(isGroupOpen(g, {}, 'Firm')).toBe(false);
		expect(isGroupOpen(g, { Firm: true }, 'Firm')).toBe(true);
	});

	it('lets a stored false beat defaultOpen: true', () => {
		expect(isGroupOpen(group({ collapsible: true }), { Firm: false }, 'Firm')).toBe(false);
	});

	it('gives the icon rail the same answer as the open sidebar', () => {
		// It used to force every group open in the rail, so that a closed group — which has no label
		// to click there — could not become unreachable. The platform's Firm settings group is
		// fourteen rows long and `defaultOpen: false`, so the effect was that COLLAPSING the sidebar
		// added fourteen icons to it: the compact chrome showed strictly more than the full one.
		// The rail is a smaller picture of the same nav now; opening the sidebar is how you reach a
		// closed group, which is the control the reader just used.
		const g = group({ collapsible: true, defaultOpen: false });
		expect(isGroupOpen(g, {}, 'Firm')).toBe(false);
		expect(isGroupOpen(g, { Firm: true }, 'Firm')).toBe(true);
	});
});

// ── The solo group ───────────────────────────────────────────────────────────────────────────────
// The platform pins a fourteen-row Firm settings group to the bottom. Opened alongside the three
// work groups it pushed them out of the viewport, so it is `solo`: while it is open the others show
// closed, and they come back as the user left them when it closes. That restoration is the whole
// design — the suppression is derived over the stored map and never written into it — so these
// assertions are what stop it degrading into "expand everything".

const work = group({ label: 'Work', collapsible: true });
const firm = group({ label: 'Firm settings', collapsible: true, defaultOpen: false, solo: true });
const nav = [group({ label: undefined }), work, firm];

describe('soloGroupOpen', () => {
	it('is false while the solo group sits closed', () => {
		expect(soloGroupOpen(nav, {}, false)).toBe(false);
	});

	it('is true once the solo group is open', () => {
		expect(soloGroupOpen(nav, { 'Firm settings': true }, false)).toBe(true);
	});

	it('is false in the rail, where there is no disclosure to be exclusive about', () => {
		expect(soloGroupOpen(nav, { 'Firm settings': true }, true)).toBe(false);
	});

	it('is false for a nav with no solo group at all', () => {
		expect(soloGroupOpen([work], { Work: true }, false)).toBe(false);
	});
});

describe('isGroupOpen — under a solo group', () => {
	it('shuts the other collapsible groups without touching their state', () => {
		const stored = { 'Firm settings': true, Work: true };
		expect(isGroupOpen(work, stored, 'Work', true)).toBe(false);
		// The same map, once the solo group closes: Work is open again because it was never rewritten.
		expect(isGroupOpen(work, stored, 'Work', false)).toBe(true);
	});

	it('restores a group to its own preference, not to open', () => {
		const stored = { 'Firm settings': true, Work: false };
		expect(isGroupOpen(work, stored, 'Work', false)).toBe(false);
	});

	it('leaves the solo group itself open', () => {
		expect(isGroupOpen(firm, { 'Firm settings': true }, 'Firm settings', true)).toBe(true);
	});

	it('never suppresses a non-collapsible group', () => {
		// Dashboard has no disclosure, so suppressing it would hide a row with no way to get it back.
		expect(isGroupOpen(nav[0], {}, '/clients', true)).toBe(true);
	});
});

describe('applyGroupOpen', () => {
	it('records a plain toggle', () => {
		expect(applyGroupOpen(nav, {}, 'Work', false)).toEqual({ Work: false });
		expect(applyGroupOpen(nav, {}, 'Firm settings', true)).toEqual({ 'Firm settings': true });
	});

	it('closes the solo group when the user opens a normal one', () => {
		// Otherwise that trigger is a control that does nothing: the click is recorded, the
		// suppression still applies, and the group the user asked for stays shut.
		expect(applyGroupOpen(nav, { 'Firm settings': true, Work: false }, 'Work', true)).toEqual({
			'Firm settings': false,
			Work: true
		});
	});

	it('ignores a close reported for a suppressed group', () => {
		// Its panel is already shut and its trigger would send `true`, so a `false` here can only be
		// the primitive syncing to the suppressed value — and honouring it would overwrite the state
		// we exist to restore.
		const stored = { 'Firm settings': true, Work: true };
		expect(applyGroupOpen(nav, stored, 'Work', false)).toBe(stored);
	});

	it('still lets a normal group close when nothing is suppressing it', () => {
		expect(applyGroupOpen(nav, { Work: true }, 'Work', false)).toEqual({ Work: false });
	});

	it('leaves the others alone when the solo group closes', () => {
		expect(
			applyGroupOpen(nav, { 'Firm settings': true, Work: true }, 'Firm settings', false)
		).toEqual({ 'Firm settings': false, Work: true });
	});

	it('closes the other solo group when one opens, and leaves the daily groups stored', () => {
		// Firm settings and Configure are both solo. Opening one must shut the other — two open
		// fourteen-row panels is the fight a single solo group exists to avoid — while Work keeps the
		// state it will be restored to.
		const configure = group({
			label: 'Configure',
			collapsible: true,
			defaultOpen: false,
			solo: true
		});
		const both = [nav[0], work, firm, configure];
		const stored = { 'Firm settings': true, Work: true };
		expect(applyGroupOpen(both, stored, 'Configure', true)).toEqual({
			'Firm settings': false,
			Work: true,
			Configure: true
		});
		const open = { 'Firm settings': false, Work: true, Configure: true };
		expect(isGroupOpen(firm, open, 'Firm settings', true)).toBe(false);
		expect(isGroupOpen(configure, open, 'Configure', true)).toBe(true);
		expect(isGroupOpen(work, open, 'Work', true)).toBe(false);
	});
});

describe('initials', () => {
	it('takes up to two words', () => {
		expect(initials('Morgan Lloyd')).toBe('ML');
		expect(initials('Graphene')).toBe('G');
		expect(initials('Mr Max Oakden')).toBe('MM');
	});

	it('ignores runs of whitespace', () => {
		expect(initials('  Morgan   Lloyd  ')).toBe('ML');
	});

	it('uses the caller fallback when there is nothing to initialise', () => {
		expect(initials('', 'G')).toBe('G');
		expect(initials('   ', '·')).toBe('·');
	});

	it('does not strip words — the brand lockup keeps every one', () => {
		// A firm called "Dr Martens" is DM, not M. Honorific stripping belongs to `personInitials`.
		expect(initials('Dr Martens')).toBe('DM');
	});
});

describe('personInitials', () => {
	it('drops a leading honorific so it agrees with the record avatar', () => {
		// The defect: the rail tile read MM for "Mr Max Oakden" while the client page's own avatar read
		// MO — two monograms for one person on one screen.
		expect(personInitials('Mr Max Oakden')).toBe('MO');
		expect(personInitials('Dr Freddie Salcombe')).toBe('FS');
		expect(personInitials('Miss Sophie Halloway')).toBe('SH');
		expect(personInitials('Mrs. Oscar Kingsley')).toBe('OK');
	});

	it('leaves a name with no honorific alone', () => {
		expect(personInitials('Andrew Gaunt')).toBe('AG');
		expect(personInitials('Futura')).toBe('F');
	});

	it('never strips away the whole name', () => {
		// Someone recorded as just "Dr" still gets a mark rather than the fallback dot.
		expect(personInitials('Dr', '·')).toBe('D');
		expect(personInitials('', '·')).toBe('·');
	});
});

// ── What the rail does differently ───────────────────────────────────────────────────────────────

describe('groupHoldsHref', () => {
	const firm = group({
		label: 'Firm settings',
		collapsible: true,
		defaultOpen: false,
		items: [
			{ label: 'Team', href: '/team' },
			{ label: 'Branding', href: '/branding' }
		]
	});

	it('opens the group holding the active item, whatever its stored state says', () => {
		// Without this the reader can be ON /branding with the rail showing no lit tile, no spinner
		// and no dot — the group renders nothing and has no label there to say it exists.
		expect(isGroupOpen(firm, {}, 'Firm settings')).toBe(false);
		expect(groupHoldsHref(firm, '/branding')).toBe(true);
	});

	it('leaves every other group as the reader set it', () => {
		expect(groupHoldsHref(firm, '/clients')).toBe(false);
	});

	it('matches the href exactly, not by prefix', () => {
		// The caller passes `activeHref`, already the longest-match winner. Re-testing prefixes here
		// would open a parent group alongside the one that actually owns the page.
		expect(groupHoldsHref(firm, '/team/roles')).toBe(false);
	});

	it('holds nothing when no item is active', () => {
		expect(groupHoldsHref(firm, '')).toBe(false);
	});
});

describe('railRuleKeys', () => {
	it('rules between two groups that both render', () => {
		const keys = railRuleKeys([
			{ key: 'a', open: true },
			{ key: 'b', open: true }
		]);
		expect([...keys]).toEqual(['b']);
	});

	it('never rules above the first thing on screen', () => {
		expect([...railRuleKeys([{ key: 'a', open: true }])]).toEqual([]);
	});

	it('skips a closed group instead of ruling above nothing', () => {
		// A closed group renders nothing in the rail — its label is hidden and its panel is shut — so
		// a rule drawn by POSITION lands above empty space, just under the account footer.
		const keys = railRuleKeys([
			{ key: 'a', open: true },
			{ key: 'firm', open: false }
		]);
		expect([...keys]).toEqual([]);
	});

	it('does not stack two rules where two adjacent groups are closed', () => {
		const keys = railRuleKeys([
			{ key: 'a', open: true },
			{ key: 'b', open: false },
			{ key: 'c', open: false },
			{ key: 'd', open: true }
		]);
		expect([...keys]).toEqual(['d']);
	});

	it('rules above the first RENDERING group, not the first declared one', () => {
		const keys = railRuleKeys([
			{ key: 'closed', open: false },
			{ key: 'first', open: true },
			{ key: 'second', open: true }
		]);
		expect([...keys]).toEqual(['second']);
	});
});
