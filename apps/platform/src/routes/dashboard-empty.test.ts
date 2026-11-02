/**
 * THE DASHBOARD'S FOUR EMPTY BOOKS.
 *
 * They were one grey sentence, and the chain that picked it asked `data.clients.length === 0`
 * BEFORE it asked about the filters. `data.clients` is `book.items` — the SERVER-NARROWED page
 * (`+page.server.ts` sends the query straight to `GET /crm/contacts`) — so a filter matching
 * nothing emptied it and a firm with four hundred clients was told:
 *
 *     "No clients yet — add your first client to start building your book."
 *
 * …and handed a create button. This is the exact defect reported from the running app, and the
 * reason it survived a whole branch about empty states is that nothing rendered this page.
 *
 * The four are: no clients at all · nothing matches the filters · not permitted to read them ·
 * the read failed. Two are failures and must not wear the neutral treatment.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Page from './+page.svelte';
import { emptyQuery } from '@graphene/ui/book/listing';

const FILTER_KEYS = ['status', 'adviser', 'type'];

const dash = (over: Record<string, unknown> = {}) => ({
	clients: [],
	matchCount: 0,
	facets: {},
	clientsError: false,
	clientsForbidden: false,
	journeys: [],
	journeysError: false,
	reviews: [],
	reviewsError: false,
	notes: [],
	notesError: false,
	...over
});

// Only what the book half of the page reads; the sidebar halves are exercised in dashboard.test.ts.
const data = (over: Record<string, unknown> = {}) => {
	const {
		query = emptyQuery(FILTER_KEYS),
		canReviews = false,
		canJourneys = false,
		canManage = true,
		...dashOver
	} = over;
	// The overview is its own stream; `null` here is a failed summary, which the book half ignores.
	return { query, canReviews, canJourneys, canManage, overview: null, dash: dash(dashOver) };
};

/** The fixture states only what the BOOK half of the page reads; the loader's `PageData` carries
 *  more (the overview's own shape, the queue's rows). Cast through the page's declared props so the
 *  narrowing is deliberate and visible, rather than an `any` that would also hide a renamed field. */
type PageProps = Parameters<typeof render<typeof Page>>[1] extends { props: infer P } ? P : never;

const body = (over: Record<string, unknown> = {}) =>
	render(Page, { props: { data: data(over) } as unknown as PageProps }).body;

/** `bg-warning-subtle` also dresses the action queue's overdue rows, so the tone is counted
 *  against the neutral page rather than asserted as present-or-absent on the whole document. */
const warningBlocks = (html: string) => html.split('bg-warning-subtle').length - 1;
const NEUTRAL_WARNINGS = warningBlocks(body());

describe('the dashboard book’s four empty states', () => {
	it('an EMPTY BOOK says so, and offers the way to start one', () => {
		const html = body();
		expect(html).toContain('No clients yet');
		expect(html).toContain('Add your first client');
		expect(html).not.toContain('match your filters');
		// Neutral: nothing has gone wrong. (This page is the baseline the two failure cases below
		// are counted against.)
		expect(html).not.toContain('Could not load your clients');
	});

	it('a read-only reader is not told to add a client they cannot add', () => {
		const html = body({ canManage: false });
		expect(html).toContain('No clients yet');
		expect(html).not.toContain('Add your first client');
	});

	it('A FILTER MATCHING NOTHING IS NOT AN EMPTY BOOK — the reported bug', () => {
		// The row count is zero in BOTH states, which is why it could never be the discriminator.
		const html = body({ query: { ...emptyQuery(FILTER_KEYS), q: 'zzzz' } });
		expect(html).toContain('No clients match your filters');
		expect(html).not.toContain('No clients yet');
		expect(html).not.toContain('Add your first client');
		// And it carries the way out, which the old sentence did not.
		expect(html).toContain('Clear filters');
	});

	it('a FAILED READ looks like a failure and offers a retry', () => {
		const html = body({ clientsError: true });
		expect(html).toContain('Your clients couldn’t be loaded');
		expect(html).toContain('Could not load your clients');
		expect(warningBlocks(html)).toBeGreaterThan(NEUTRAL_WARNINGS);
		expect(html).toContain('Try again');
		expect(html).not.toContain('No clients yet');
	});

	it('a PERMISSION REFUSAL is its own state — not an outage, and not an empty book', () => {
		// A 403 is permanent: retrying cannot fix it, and an administrator can. The loader used to
		// fold it into `clientsError`, so it read as an outage and offered a retry that would fail
		// for ever.
		const html = body({ clientsForbidden: true });
		expect(html).toContain('You can’t read this firm’s clients');
		expect(html).toContain('An administrator at your firm can add it');
		expect(warningBlocks(html)).toBeGreaterThan(NEUTRAL_WARNINGS);
		expect(html).not.toContain('Try again');
		expect(html).not.toContain('No clients yet');
	});
});
