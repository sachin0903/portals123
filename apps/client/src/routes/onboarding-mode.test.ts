import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import { portalNav } from '$lib/nav';
import { load } from './+page.server';

type LoadArg = Parameters<typeof load>[0];

describe('client portal — active onboarding mode', () => {
	it('lands an invited client on onboarding instead of an empty home', async () => {
		await expect(
			load({
				locals: { user: { id: 'u1', email: 'client@example.test' } },
				parent: async () => ({ activeContactId: 1, onboarding: true }),
				depends: vi.fn()
			} as unknown as LoadArg)
		).rejects.toMatchObject({ status: 303, location: '/onboarding' });
	});

	it('names the nav tab for the journey when it is not account opening', () => {
		expect(portalNav(null, true, 'Fact find')).toEqual([
			{ label: 'Fact find', href: '/onboarding', iconName: 'clipboard-list' }
		]);
	});

	it('reduces the global navigation to Onboarding while the journey is active', () => {
		// Asserted on the decision, not on the layout's source. This used to scan `+layout.svelte`
		// for one exact ternary and failed the moment the same choice was written as an early
		// return — a green-to-red with no behaviour change in it. The derivation is `$lib/nav.ts`
		// for that reason.
		expect(portalNav(null, true)).toEqual([
			{ label: 'Onboarding', href: '/onboarding', iconName: 'clipboard-list' }
		]);
	});

	it('keeps the firm’s own pages, plus Insights, once onboarding is done', () => {
		const items = portalNav(null, false);
		expect(items.length).toBeGreaterThan(1);
		expect(items.map((i) => i.href)).toContain('/insights');
		// `/insights` is a built-in route, so a firm may also name a page `insights`; one route must
		// not appear twice in the bar.
		const placed = portalNav(
			[
				{
					id: 'pg_insights',
					slug: 'insights',
					label: 'Reading',
					icon: 'newspaper',
					nav: true,
					layouts: { default: [] }
				}
			],
			false
		);
		expect(placed.filter((i) => i.href === '/insights')).toHaveLength(1);
	});

	it('leads with a journey and the client’s required actions', () => {
		const source = readFileSync(new URL('./onboarding/+page.svelte', import.meta.url), 'utf8');
		expect(source).toContain('Your journey');
		expect(source).toContain('What you need to do');
		expect(source).toContain("'Onboarding journey'");
		expect(source).toContain('These are the stages of this journey.');
	});

	it('shows what the client is applying for without a click', () => {
		// The review used to sit behind a four-tab strip, so the accounts a client is agreeing to
		// were one `?section=accounts` away from someone who did not know the tabs were there. What
		// they are signing for cannot be the thing that needs discovering, so the summary is
		// unconditional markup and there is no tab strip to put it back behind.
		const source = readFileSync(new URL('./onboarding/+page.svelte', import.meta.url), 'utf8');
		expect(source).toContain('What you’re applying for');
		expect(source).not.toContain('Segmented');
		expect(source).not.toContain("params.set('section'");
	});

	it('keys review records by public nanoid, never by presentation text', () => {
		// Duplicate display names are valid: an application may contain two SIPPs with the same
		// default name, two people may share a name and role, and beneficiaries may repeat. A
		// duplicate keyed-each throws during hydration and clears the whole onboarding page.
		const source = readFileSync(new URL('./onboarding/+page.svelte', import.meta.url), 'utf8');
		expect(source).toContain('{#each review.accounts as account (account.uid)}');
		expect(source).toContain('{#each review.applicants as person (person.uid)}');
		// Beneficiaries carry no uid of their own and may repeat name-for-name, so their key is the
		// index: stable for a static review list, and never a duplicate.
		expect(source).toContain('{#each account.beneficiaries as b, i (i)}');
		expect(source).not.toContain('account.name + account.product');
		expect(source).not.toContain('person.name + person.role');
		expect(source).not.toContain('b.first_name + b.surname');
	});
});
