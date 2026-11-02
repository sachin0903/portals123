/**
 * The ordering rule behind the clients listing's Export bug: two URL changes from one click must
 * build on each other, not both on the URL as it stood before either landed.
 */
import { describe, expect, it, vi } from 'vitest';
import { createListingNav } from './nav.js';

/** A navigator that does not settle until told, so a click's second change lands mid-flight. */
function heldNav() {
	const searches: string[] = [];
	let settle: (() => void) | undefined;
	const nav = createListingNav((search) => {
		searches.push(search);
		return new Promise<void>((resolve) => {
			settle = resolve;
		});
	});
	return { nav, searches, settle: () => settle?.() };
}

describe('createListingNav', () => {
	it('builds the second change of a click on the first, not on the stale URL', async () => {
		// The real sequence: leaving the Closed population deletes `segment`, then the status filter
		// is applied. Reading the URL for the second one put `segment=closed` straight back.
		const url = new URLSearchParams('segment=closed&status=');
		const { nav, searches } = heldNav();

		const afterSegment = nav.params(url);
		afterSegment.delete('segment');
		nav.commit(afterSegment);

		const afterStatus = nav.params(url);
		afterStatus.set('status', 'active');
		nav.commit(afterStatus, true);

		expect(searches).toHaveLength(2);
		expect(searches[1]).toBe('status=active');
		expect(searches[1]).not.toContain('segment');
	});

	it('goes back to the URL once the navigation has landed', async () => {
		const { nav, searches, settle } = heldNav();
		const first = nav.params(new URLSearchParams('status=active'));
		first.set('status', 'onboarding');
		nav.commit(first);
		settle();
		await Promise.resolve();
		await Promise.resolve();

		// A later click reads the URL again — the page has navigated, so the URL is the truth now
		// and a held `pending` would make the listing answer for a click the reader has moved on from.
		const later = nav.params(new URLSearchParams('status=onboarding&adviser=priya'));
		expect(later.get('adviser')).toBe('priya');
		expect(searches).toEqual(['status=onboarding']);
	});

	it('does not strand the base when a navigation fails', async () => {
		const nav = createListingNav(() => Promise.reject(new Error('navigation cancelled')));
		const params = new URLSearchParams('status=active');
		nav.commit(params);
		await Promise.resolve();
		await Promise.resolve();
		// A rejected goto must not leave every later click building on the params it never reached.
		expect(nav.params(new URLSearchParams('status=closed')).get('status')).toBe('closed');
	});

	it('passes the caller’s own replaceState through', () => {
		const go = vi.fn();
		const nav = createListingNav(go);
		nav.commit(new URLSearchParams('a=1'));
		nav.commit(new URLSearchParams('a=2'), true);
		expect(go.mock.calls[0][1]).toEqual({ replaceState: false });
		expect(go.mock.calls[1][1]).toEqual({ replaceState: true });
	});
});
