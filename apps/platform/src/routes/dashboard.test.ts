/**
 * The dashboard's two pure halves.
 *
 *  - `@graphene/ui/book/dashboard` (`book.ts`) — the clients-book table's derived status, focus segment
 *    and filter predicate. The dashboard and `/clients` render the same table off these, so a change
 *    that would make the two screens disagree fails here.
 *  - `@graphene/ui/book/dashboard` (`action-queue.ts`) — the rail's "needs you" list, built from the
 *    caller's own onboarding journeys and the reviews awaiting them.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ALL } from '@graphene/ui/book/listing';
import { BOOK_COLUMNS, filterBook, statusOf, type BookRow } from '@graphene/ui/book/dashboard';
import { journeyItems, railItems, reviewItems } from '@graphene/ui/book/dashboard';
import type { AppExecutionRow } from '@graphene/ui/book/journeys';
import type { ReviewRow } from '@graphene/ui/book/reviews';

const client = (over: Partial<BookRow> = {}): BookRow => ({
	id: 1,
	uid: 'con_abc123',
	type: 'individual',
	name: 'Theo Caldwell',
	title: null,
	first_name: 'Theo',
	surname: 'Caldwell',
	email: 'theo@caldwell.test',
	advisers: ['Zoe Lead'],
	enabled: true,
	service_type: 'advised',
	account_count: 2,
	valued_account_count: 1,
	portfolio_value: 100_000,
	cash: 2_500,
	growth: 1_200,
	...over
});

const NO_FILTERS = { query: '', status: ALL, adviser: ALL, type: ALL };

describe('statusOf', () => {
	it('is derived, not stored: no account ⇒ lead, else active', () => {
		expect(statusOf(client())).toBe('active');
		expect(statusOf(client({ account_count: 0 }))).toBe('lead');
	});

	it('Closed wins, then Suspended from the custodian — never from enabled', () => {
		expect(statusOf(client({ deleted_at: '2026-09-11T00:00:00Z' }))).toBe('closed');
		expect(statusOf(client({ provider_status: 'suspended' }))).toBe('suspended');
		expect(statusOf(client({ enabled: false }))).toBe('active');
	});
});

describe('filterBook', () => {
	it('searches the fields staff quote on a call — name, reference, email, adviser', () => {
		const c = client();
		for (const query of ['cald', 'CON_ABC', 'theo@', 'zoe']) {
			expect(filterBook([c], { ...NO_FILTERS, query }), query).toEqual([c]);
		}
		expect(filterBook([c], { ...NO_FILTERS, query: 'nobody' })).toEqual([]);
	});

	it('filters on the DERIVED status, so the badge and the filter cannot disagree', () => {
		const book = [
			client({ id: 1 }),
			client({ id: 2, account_count: 0 }),
			client({ id: 3, provider_status: 'suspended' })
		];
		expect(filterBook(book, { ...NO_FILTERS, status: 'lead' }).map((c) => c.id)).toEqual([2]);
		expect(filterBook(book, { ...NO_FILTERS, status: 'suspended' }).map((c) => c.id)).toEqual([3]);
	});

	it('matches ANY adviser on a jointly-serviced client', () => {
		const shared = client({ advisers: ['Zoe Lead', 'Sam Second'] });
		expect(filterBook([shared], { ...NO_FILTERS, adviser: 'Sam Second' })).toEqual([shared]);
		expect(filterBook([shared], { ...NO_FILTERS, adviser: 'Nobody' })).toEqual([]);
	});

	it('ignores the status filter for closed rows — every closed row reads as Closed', () => {
		const archived = client({ account_count: 0 });
		expect(filterBook([archived], { ...NO_FILTERS, status: 'active' })).toEqual([]);
		expect(filterBook([archived], { ...NO_FILTERS, status: 'active' }, { archived: true })).toEqual(
			[archived]
		);
	});

	it('combines every predicate (all must hold)', () => {
		const book = [
			client({ id: 1, surname: 'Caldwell', service_type: 'advised' }),
			client({ id: 2, surname: 'Caldwell', type: 'organisation', name: 'Caldwell Trust' })
		];
		expect(
			filterBook(book, { ...NO_FILTERS, query: 'caldwell', type: 'organisation' }).map((c) => c.id)
		).toEqual([2]);
	});
});

describe('BOOK_COLUMNS', () => {
	it('gives every formatted column an explicit sortValue, so order matches what is shown', () => {
		const formatted = ['name', 'adviser', 'value', 'accounts', 'status'];
		for (const key of formatted) {
			const column = BOOK_COLUMNS.find((c) => c.key === key);
			expect(column, key).toBeDefined();
			expect(column?.sortValue, key).toBeTypeOf('function');
		}
	});

	it('sorts a client by display name, not by the raw surname field', () => {
		const column = BOOK_COLUMNS.find((c) => c.key === 'name');
		expect(column?.sortValue?.(client({ type: 'organisation', name: 'Acme Trust' }))).toBe(
			'Acme Trust'
		);
	});
});

// ---------------------------------------------------------------------------
// The rail
// ---------------------------------------------------------------------------

const journey = (over: Partial<AppExecutionRow> = {}): AppExecutionRow => ({
	uid: 'exe_1',
	status: 'running',
	created_at: '2026-07-01T09:00:00Z',
	application_status: 'fact_find',
	flow_name: 'Onboarding',
	flow_code: 'onboarding',
	adviser_name: 'Gio Matonti',
	assignee_name: 'Gio Matonti',
	clients: 1,
	accounts: 0,
	steps_total: 8,
	steps_done: 3,
	client_names: 'Mr Max Oakden',
	open_client_steps: 0,
	...over
});

const review = (over: Partial<ReviewRow> = {}): ReviewRow =>
	({
		uid: 'rev_1',
		case_no: 1042,
		status: 'pending',
		action_key: 'contact.bank_details_changed',
		label: 'Bank details',
		proposed_at: '2026-07-20T09:00:00Z',
		contact_name: 'Mr Max Oakden',
		account_name: 'GIA-00421',
		subject_type: 'contact',
		...over
	}) as ReviewRow;

describe('journeyItems', () => {
	it('names who is being onboarded, the stage and the progress', () => {
		const [item] = journeyItems([journey()]);
		expect(item.title).toBe('Mr Max Oakden');
		expect(item.detail).toBe('Onboarding · Applicant details · 3 of 8');
		expect(item.href).toBe('/journeys/exe_1');
	});

	it('says whose move it is', () => {
		// The old rail could not: every request read "Awaiting client" whatever the truth was.
		expect(journeyItems([journey()])[0].ball).toBe('staff');
		expect(journeyItems([journey({ open_client_steps: 2 })])[0].ball).toBe('client');
	});

	it('keeps only live onboarding', () => {
		const rows = journeyItems([
			journey({ uid: 'done', status: 'completed' }),
			journey({ uid: 'stopped', status: 'cancelled' }),
			journey({ uid: 'other', flow_code: 'annual_review', flow_name: 'Annual review' }),
			journey({ uid: 'live' })
		]);
		expect(rows.map((i) => i.key)).toEqual(['journey:live']);
	});

	it('falls back to a generic name rather than rendering a blank row', () => {
		expect(journeyItems([journey({ client_names: null })])[0].title).toBe('New client');
	});
});

describe('reviewItems', () => {
	it('names the case, the change and who it concerns', () => {
		// "Peer review · a name" was all the old rail said, which is why every row read the same.
		const [item] = reviewItems([review()]);
		expect(item.title).toContain('RV-1042');
		expect(item.detail).toMatch(/^Mr Max Oakden · GIA-00421 · waiting /);
		expect(item.href).toBe('/reviews?open=rev_1');
		expect(item.ball).toBeNull();
	});

	it('drops anything no longer pending', () => {
		expect(reviewItems([review({ status: 'approved' })])).toEqual([]);
	});
});

describe('railItems', () => {
	it('puts work that needs the reader first, and the oldest of it above a newer review', () => {
		// A journey started in March has waited longer than a review raised in July; burying it
		// under newer items is how a list teaches you to ignore it.
		const rail = railItems(
			[
				journey({ uid: 'today', created_at: '2026-07-28T09:00:00Z', open_client_steps: 1 }),
				journey({ uid: 'march', created_at: '2026-03-01T09:00:00Z' })
			],
			[review()]
		);
		expect(rail.map((i) => i.key)).toEqual(['journey:march', 'review:rev_1', 'journey:today']);
	});

	it('is empty — not broken — when both sources are empty', () => {
		expect(railItems([], [])).toEqual([]);
	});
});

describe('the loading placeholder', () => {
	// Asserted as a source shape, the way `branding-editor.test.ts` does: the page needs a live
	// route to render and what is worth pinning here is which primitive draws the bars.
	const source = readFileSync(
		join(
			import.meta.dirname,
			'../../../../pkgs/ui/src/lib/components/book/dashboard/firm-dashboard.svelte'
		),
		'utf8'
	);
	const pending = /\{#snippet dashPending\(\)\}[\s\S]*?\{\/snippet\}/.exec(source)?.[0] ?? '';
	/** The KPI tiles' own placeholder: the overview streams separately from the book (`dash.ts`),
	 *  so its bars are their own snippet, drawn while `/crm/overview` is in flight. */
	const tiles = /\{#snippet tilesPending\(\)\}[\s\S]*?\{\/snippet\}/.exec(source)?.[0] ?? '';
	/** Comments out, because they quote the very attributes these assertions count. */
	const markup = pending.replace(/<!--[\s\S]*?-->/g, '');
	const tilesMarkup = tiles.replace(/<!--[\s\S]*?-->/g, '');

	it('draws bars with the shared Skeleton, not a faint hand-rolled pulse', () => {
		// It used to be three `bg-muted/40 animate-pulse` blocks. The muted token at 40% sits barely
		// off the canvas, and a pulse takes a block that faint DOWN towards the background at the
		// bottom of every cycle — so the dashboard read as an empty page, not a loading one.
		// `Skeleton` is full-strength `bg-muted` with the travelling sweep, and it is the one
		// definition the other placeholders in the product already share.
		expect(pending, 'dashPending snippet not found').not.toBe('');
		expect(tiles, 'tilesPending snippet not found').not.toBe('');
		for (const m of [markup, tilesMarkup]) {
			expect(m).toContain('<Skeleton');
			expect(m).not.toContain('animate-pulse');
			expect(m).not.toContain('bg-muted/40');
		}
	});

	it('is the shape that is arriving, so nothing jumps when it lands', () => {
		// Four KPI tiles on StatTile's own `glass-panel` surface, the book as the same
		// toolbar-table-pager `ListingSkeleton` the real listing resolves into, and the queue.
		expect(tilesMarkup).toContain('glass-panel');
		expect(tilesMarkup).toContain('lg:grid-cols-4');
		expect(markup).toContain('<ListingSkeleton');
		expect(markup).toContain('lg:grid-cols-[minmax(0,1fr)_22rem]');
	});

	it('announces itself once', () => {
		// ListingSkeleton carries its own role="status"; nested inside this one, a screen reader
		// would hear "Loading" twice. The frame owns the status and the listing cell is hidden.
		// The tiles' placeholder, on its own stream, is hidden too rather than a second status —
		// one "Loading" for the page, not one per stream.
		expect(markup.match(/role="status"/g)).toHaveLength(1);
		expect(markup).toMatch(/aria-hidden="true">\s*<ListingSkeleton/);
		expect(tilesMarkup).not.toContain('role="status"');
		expect(tilesMarkup).toContain('aria-hidden="true"');
	});

	it('streams the overview apart from the book, so the hero does not wait for it', () => {
		// Two `Streamed` mounts: `overview` for the tiles, `dash` for the book and the queue. One
		// mount reading `d.overview` is the shape that held the "£7.8m" back behind the slowest of
		// the four reads.
		expect(source).toMatch(/<Streamed data=\{overview\}/);
		expect(source).toMatch(/<Streamed data=\{dash\}/);
		expect(source).not.toContain('d.overview');
	});
});
