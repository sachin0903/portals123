/**
 * The currency keeps its flag when its column folds.
 *
 * Below 80rem the Currency column steps out and its code moves under the amount. It went as bare
 * text, so the same fact read as a flag pill on a wide table and as three letters the moment the
 * detail panel opened beside it.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import TransactionsListing from './transactions-listing.svelte';
import { transactionFixtures } from './transaction-fixtures.js';

const links = { account: (uid: string) => `/a/${uid}`, client: (uid: string) => `/c/${uid}` };

describe('TransactionsListing currency (SSR)', () => {
	it('draws the flag beside the code on the line that stands in for the folded column', () => {
		const row = { ...transactionFixtures[0], currency: 'USD' };
		const body = render(TransactionsListing, { props: { transactions: [row], links } }).body;
		// The stand-in is the element shown exactly while the column is hidden.
		const at = body.indexOf('block @7xl:hidden');
		expect(at).toBeGreaterThan(-1);
		const standIn = body.slice(at, body.indexOf('</span></span>', at));
		expect(standIn).toMatch(/<img[^>]*data-flag="USD"[^>]*>(<!--[^>]*-->)*USD$/);
	});
});
