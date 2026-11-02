/** The account record's section list, and that every row lands on a route that exists. */
import { describe, expect, it } from 'vitest';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
	ACCOUNT_TAB_ROUTES,
	accountSections,
	accountSidebarFocus,
	accountTabTarget
} from './account-sections';
import { FOCUS_ICONS, parseFocus } from './sidebar-focus';

const repoRoot = fileURLToPath(new URL('../../../../../', import.meta.url));

function sectionDirs(relative: string): string[] {
	return readdirSync(join(repoRoot, relative), { withFileTypes: true })
		.filter((e) => e.isDirectory())
		.map((e) => e.name);
}

// A uid, not the serial `id: 7` this fixture used to carry — the sections build URLs.
const ACCOUNT = { uid: 'acc_7f3a', name: 'Oakden SIPP', subtitle: 'SIPP · Mr Max Oakden' };

describe('accountSections', () => {
	it('points every section at this account and highlights Overview only on the record itself', () => {
		const sections = accountSections(ACCOUNT);
		expect(sections.every((s) => s.href.startsWith('/accounts/acc_7f3a'))).toBe(true);
		const overview = sections.find((s) => s.label === 'Overview')!;
		// Without `exact`, Overview stays highlighted on Holdings and Transactions too.
		expect(overview).toMatchObject({ href: '/accounts/acc_7f3a', exact: true });
		expect(sections.filter((s) => s.exact)).toHaveLength(1);
	});

	it('carries the three bands that used to be tabs', () => {
		expect(accountSections(ACCOUNT).map((s) => s.label)).toEqual([
			'Overview',
			'Holdings',
			'Transactions'
		]);
	});

	it('asks only for icons the shell actually has', () => {
		// An unknown key degrades to no icon at all, which reads as a half-loaded sidebar.
		for (const s of accountSections(ACCOUNT)) expect(FOCUS_ICONS[s.icon!], s.label).toBeTruthy();
	});

	it('lands every section on a route that exists', () => {
		// The nav and the route tree are two lists that have to agree; a section with no directory is
		// a row that 404s on click. Read from disk rather than restated, so adding one to the nav and
		// forgetting the page fails here.
		const trees = [
			'apps/platform/src/routes/accounts/[uid]',
			'apps/admin/src/routes/tenants/[uid]/operate/accounts/[accountUid]'
		];
		for (const s of accountSections(ACCOUNT)) {
			const tail = s.href.replace('/accounts/acc_7f3a', '').replace(/^\//, '');
			if (!tail) continue; // the overview IS the record's own +page.svelte
			for (const tree of trees) expect(sectionDirs(tree), `${tree} ${s.label}`).toContain(tail);
		}
	});
});

describe('accountSidebarFocus', () => {
	it('survives the trip through page.data', () => {
		// `sidebarFocus` is serialised and re-parsed by the shell, which treats it as untrusted —
		// a shape the parser rejects renders no focused nav at all.
		const parsed = parseFocus(JSON.parse(JSON.stringify(accountSidebarFocus(ACCOUNT))));
		expect(parsed?.title).toBe('Oakden SIPP');
		expect(parsed?.eyebrow).toBe('Account');
		expect(parsed?.sections).toHaveLength(3);
	});

	it('goes UP to the owner when there is one, and to the list when there is not', () => {
		// An account is reached from its client far more often than from the list.
		const owned = accountSidebarFocus({
			...ACCOUNT,
			back: { href: '/clients/42', label: 'Mr Max Oakden' }
		});
		expect(owned.back).toEqual({ href: '/clients/42', label: 'Mr Max Oakden' });
		expect(accountSidebarFocus(ACCOUNT).back).toEqual({ href: '/accounts', label: 'Accounts' });
	});
});

describe('accountTabTarget', () => {
	it('resolves each old tab name to its route', () => {
		expect(accountTabTarget('acc_7f3a', 'holdings')).toBe('/accounts/acc_7f3a/holdings');
		expect(accountTabTarget('acc_7f3a', 'transactions')).toBe('/accounts/acc_7f3a/transactions');
		expect(accountTabTarget('acc_7f3a', 'overview')).toBe('/accounts/acc_7f3a');
	});

	it('lands anything unrecognised on the record itself, never a 404', () => {
		expect(accountTabTarget('acc_7f3a', 'nonsense')).toBe('/accounts/acc_7f3a');
		expect(accountTabTarget('acc_7f3a', null)).toBe('/accounts/acc_7f3a');
		expect(accountTabTarget('acc_7f3a', '')).toBe('/accounts/acc_7f3a');
	});

	it('covers every section', () => {
		// The two maps are the same list twice; one gaining a member and not the other is a dead nav
		// row or an unreachable route.
		const fromSections = accountSections(ACCOUNT).map((s) =>
			s.href.replace('/accounts/acc_7f3a', '').replace(/^\//, '')
		);
		expect(fromSections.sort()).toEqual(
			Object.values(ACCOUNT_TAB_ROUTES)
				.map((r) => r.replace(/^\//, ''))
				.sort()
		);
	});
});
