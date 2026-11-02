/**
 * SSR pins for the shared clients listing: links are INJECTED (no hardcoded hrefs — the admin
 * portal mounts this under /tenants/[id]/book), and mutations only exist when `actions` is
 * passed — absent, the listing is read-only (no Reopen column, no New client). Create is a
 * separate name from restore, so restore-only actions never grow a mint button.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import ClientsListing from './clients-listing.svelte';
import { emptyQuery } from '../listing/query.js';
import type { ClientListRow } from './types';

const client = (over: Partial<ClientListRow> = {}): ClientListRow => ({
	id: 1,
	uid: 'con_abc123',
	type: 'individual',
	service_type: 'advised',
	enabled: true,
	name: 'Theo Caldwell',
	title: null,
	first_name: 'Theo',
	surname: 'Caldwell',
	email: 'theo@caldwell.test',
	mobile: null,
	address: null,
	account_count: 2,
	account_types: ['GIA', 'ISA'],
	client_type: 'individual',
	advisers: ['Zoe Lead'],
	portfolio_value: 125_000,
	cash: 4_200,
	growth: 8_000,
	...over
});

const links = { client: (uid: string) => `/tenants/t_1/book/clients/${uid}` };
const enhance = () => ({ destroy() {} });
const actions = { enhance, restore: '?/restoreClient' };

/** The component's source from `baseColumns` on — NOT the whole file. The filter bar declares its
 *  own `{ key, label }` entries and they sit above the columns, so an unscoped
 *  `indexOf("key: 'status'")` finds the FILTER called status and asserts column rules against it;
 *  an unscoped `key: 'adviser'` ran one slice backwards and matched nothing at all. */
const columnSource = (): string => {
	const file = readFileSync(join(import.meta.dirname, 'clients-listing.svelte'), 'utf8');
	return file.slice(file.indexOf('const baseColumns'));
};

describe('ClientsListing (SSR)', () => {
	it('renders every row through the injected link builder — no hardcoded hrefs', () => {
		const body = render(ClientsListing, {
			props: { clients: [client(), client({ id: 2, uid: 'con_x', surname: 'Beaumont' })], links }
		}).body;
		expect(body).toContain('href="/tenants/t_1/book/clients/con_abc123"');
		expect(body).toContain('href="/tenants/t_1/book/clients/con_x"');
		expect(body).toContain('Theo Caldwell');
		// The reference is NOT on a row any more — it moved to the record header, where the rest
		// of "how do I refer to this client" lives. A listing is scanned for names and money.
		expect(body).not.toContain('ID:');
		expect(body).not.toContain('href="/clients/con_abc123"');
		// The serial id must not reach a URL — it is the fixture's `id: 1`, and a builder handed
		// the wrong field would still render a plausible-looking href.
		expect(body).not.toContain('/book/clients/1"');
	});

	it('pages complete-array mounts at 25 rows', () => {
		const clients = Array.from({ length: 26 }, (_, index) =>
			client({
				id: index + 1,
				uid: `con_${index + 1}`,
				first_name: `Client${index + 1}`,
				surname: 'Page'
			})
		);
		const body = render(ClientsListing, { props: { clients, links } }).body;
		expect(body).toContain('1–25 of 26');
		expect(body).toContain('Client25 Page');
		expect(body).not.toContain('Client26 Page');
	});

	// New client is gated on `actions.create`, not merely on `actions` being present — restore
	// is a different permission-shaped act, and a read-only mount must never offer one.
	it('offers New client only when the create action is passed', () => {
		// Creation is the page's primary act, so it is a named button at the table edge rather than
		// an item hidden behind an ambiguous kebab.
		const readOnly = render(ClientsListing, { props: { clients: [client()], links } }).body;
		const restoreOnly = render(ClientsListing, {
			props: { clients: [client()], links, actions }
		}).body;
		const writable = render(ClientsListing, {
			props: {
				clients: [client()],
				links,
				actions: { ...actions, create: '?/createClient' }
			}
		}).body;
		expect(readOnly).not.toContain('New client');
		expect(restoreOnly).not.toContain('New client');
		expect(writable).toContain('New client');
		const source = readFileSync(join(import.meta.dirname, 'clients-listing.svelte'), 'utf8');
		expect(source).toContain('New client');
		expect(source).not.toContain('ListingOverflow');
	});

	it('create-only actions offer New client and never Reopen', () => {
		const props = {
			clients: [],
			archived: [client({ enabled: false })],
			links,
			initialSegment: 'archived' as const,
			actions: { enhance, create: '?/createClient' }
		};
		const body = render(ClientsListing, { props }).body;
		expect(body).toContain('New client');
		expect(body).not.toContain('Reopen');
	});

	it('the create drawer is a form drawer, not the illustration floor', () => {
		const source = readFileSync(join(import.meta.dirname, 'clients-listing.svelte'), 'utf8');
		expect(source).toContain('sm:max-w-xl');
		expect(source).toContain('create?: string');
		expect(source).toContain('restore?: string');
	});

	it('creation asks for the PRIMARY MOBILE, and does not offer the other-numbers list', () => {
		// The legacy `phone` landline value is ignored: the number creation asks for is the one staff
		// ring. The other numbers are a repeatable list and a detail-page job — a New client drawer
		// that opens with an "Add number" button asks for work nobody has on the call.
		const source = readFileSync(join(import.meta.dirname, 'clients-listing.svelte'), 'utf8');
		expect(source).toContain('id="new-client-mobile"');
		expect(source).toContain('name="mobile"');
		expect(source).toContain('Mobile number');
		expect(source).not.toContain('new-client-phone');
		expect(source).not.toContain('PhoneNumbersInput');
	});

	it('the closed segment offers Reopen only with actions — and still links the record', () => {
		const props = {
			clients: [],
			archived: [client({ enabled: false })],
			links,
			initialSegment: 'archived' as const
		};
		const readOnly = render(ClientsListing, { props }).body;
		expect(readOnly).not.toContain('Reopen');
		expect(readOnly).not.toContain('Restore');
		// Closed stays loadable, so the name is still an anchor — the serial id is not.
		expect(readOnly).toContain('href="/tenants/t_1/book/clients/con_abc123"');
		expect(readOnly).not.toContain('/book/clients/1"');
		expect(readOnly).toContain('Closed');
		expect(readOnly).not.toContain('Archived');

		const writable = render(ClientsListing, { props: { ...props, actions } }).body;
		expect(writable).toContain('Reopen');
		expect(writable).not.toContain('Restore');
	});

	it('the closed-book empty state never offers New client', () => {
		const source = readFileSync(join(import.meta.dirname, 'clients-listing.svelte'), 'utf8');
		expect(source).toContain('canCreateClient && !archivedView && source.length === 0');
	});

	it('all three listings count a SEARCH TERM as a narrowing, not just their dropdowns', () => {
		// A SOURCE SCAN, because the state it guards cannot be rendered: `localQuery` has no prop
		// to seed it — the toolbar writes it — so SSR can never reach a local-mode search miss.
		//
		// `dirty` carries the filter dropdowns only. accounts-listing and transactions-listing both
		// gained `dirty || term` on this branch and clients-listing did not, so its CLOSED segment
		// — the one local-mode state it has — answered a search matching nothing with "Nothing
		// closed" and no Clear action, the exact shape the other two were changed to avoid. What
		// is worth pinning is the CONSISTENCY: three components, one rule.
		const dir = new URL('../', import.meta.url);
		for (const file of [
			'clients/clients-listing.svelte',
			'accounts/accounts-listing.svelte',
			'accounts/transactions-listing.svelte'
		]) {
			const source = readFileSync(new URL(file, dir), 'utf8');
			expect(source, `${file} must fold the search term into its empty-state predicate`).toMatch(
				/(narrowed = \$derived\(dirty \|\| term|dirty \|\| term)/
			);
		}
	});

	it('a failed read states an error, never an empty book', () => {
		const body = render(ClientsListing, { props: { clients: [], links, loadError: true } }).body;
		// The phrase is what tells the reader this is not their book being empty. Asserted exactly:
		// a case-folded match was a real loosening, and it would have gone on passing if the
		// sentence were rebuilt into something a reader parses differently.
		expect(body).toContain('Clients could not be loaded.');
		// AND IT LOOKS LIKE A FAILURE, which the sentence alone could not do. A reader matches the
		// shape before the words, so the two failure kinds wear `LoadErrorBanner`'s tokens; without
		// this a wording change could quietly return the failure to looking like an empty book.
		expect(body).toContain('bg-warning-subtle');
	});

	it('a filter that matches nothing does not claim the book is empty', () => {
		// THE PAGE IS NOT THE BOOK. In server mode `clients` is one page, so a filter matching
		// nothing empties it — and discriminating on that array's length then says "No clients yet"
		// to a firm with a full book. It survived unnoticed while the empty state was one sentence;
		// adding the Clear action beside it made the box say three things at once, two of them false.
		const body = render(ClientsListing, {
			props: {
				clients: [],
				links,
				listing: { ...emptyQuery(['status']), q: 'zzzz' },
				matchCount: 0,
				onquery: () => {}
			}
		}).body;
		expect(body).toContain('No clients match your filters');
		expect(body).not.toContain('No clients yet');
		expect(body).not.toContain('Add your first client');
	});

	it('an empty book and a failed read do not look alike', () => {
		const empty = render(ClientsListing, { props: { clients: [], links } }).body;
		const failed = render(ClientsListing, { props: { clients: [], links, loadError: true } }).body;
		expect(empty).toContain('No clients yet');
		expect(empty).not.toContain('bg-warning-subtle');
		expect(failed).not.toContain('No clients yet');
	});

	it('has no page heading — the topbar crumb is the title', () => {
		const body = render(ClientsListing, {
			props: { clients: [client(), client({ id: 2, portfolio_value: 75_000 })], links }
		}).body;
		expect(body).not.toContain('Everyone your firm advises');
		expect(body).not.toContain('under advice');
		expect(body).not.toMatch(/\d+ of \d+ clients/);
	});

	it('has no Live book / Archived switch — Closed is a status', () => {
		// Two controls narrowing by the same idea, and the switch was the one you had to NOTICE to
		// understand why a client was missing. It is a status now, so the filter that already answers
		// "which clients" answers this too.
		const body = render(ClientsListing, { props: { clients: [client()], links } }).body;
		expect(body).not.toContain('Live book');
		expect(body).toContain('lucide-funnel');
		// The OPTION is read off the source: a closed Select renders no panel at SSR, so asserting the
		// label in the body would pass for the wrong reason (or, as it did, fail for the wrong one).
		const source = readFileSync(join(import.meta.dirname, 'clients-listing.svelte'), 'utf8');
		expect(source).toContain("{ value: 'closed', label: 'Closed' }");
		expect(source).not.toContain("{ value: 'archived', label: 'Archived' }");
		// No source-level ban on "Live book": the comment explaining why the switch went says the words,
		// and a raw scan flags the explanation as the defect — the same false positive
		// switch-columns.test.ts and field-shell.test.ts both record hitting. The rendered body above is
		// what proves the control is gone.
	});

	it('drops the client-type column, but keeps the filter that narrows by it', () => {
		// The column showing the SPECIFIC kind (trust / company / charity, not just individual-vs-
		// organisation) was removed again on request — the avatar still carries the person/
		// organisation cue beside the name, and the filter is still how the book is narrowed by the
		// finer distinction; only the row's own cell for it is gone.
		const body = render(ClientsListing, {
			props: {
				clients: [
					client(),
					client({ id: 2, type: 'organisation', client_type: 'trust', name: 'Oakden Trust' })
				],
				links
			}
		}).body;
		expect(body).not.toContain('>Client type<');
		expect(body).toContain('lucide-funnel');
	});

	it('shows the wrappers a client holds as tags, and counts the overflow', () => {
		// The column was a bare `account_count`, which cannot answer the question a book is scanned
		// for ("which of my clients has a SIPP?"). The tags can. `AdviserStack`'s idiom for the
		// overflow — the first few, then `+N` — because a cell sharing a row with three money
		// columns cannot be as wide as the busiest client's list.
		const body = render(ClientsListing, {
			props: {
				clients: [client({ account_types: ['GIA', 'ISA', 'JISA', 'SIPP'], account_count: 6 })],
				links
			}
		}).body;
		for (const wrapper of ['GIA', 'ISA', 'JISA']) expect(body).toContain(`>${wrapper}<`);
		// The fourth is a count, not a badge…
		expect(body).toContain('+1');
		// …and the full list is still announced, so the summary does not become the whole answer for
		// anyone not using a mouse.
		expect(body).toContain('GIA, ISA, JISA, SIPP');
		expect(body).toContain('6 accounts');
	});

	it('prints the date of birth under the name, else where the client is, else the name alone', () => {
		// The second line is what tells two Alan Hornes apart and what a custodian asks for first.
		// A client with no date — an organisation — gets the record header's address line without
		// its street (town, postcode, country), so the row still says who this is.
		const born = render(ClientsListing, {
			props: { clients: [client({ dob: '1962-08-05' })], links }
		}).body;
		expect(born).toContain('5 Aug 1962');

		const located = render(ClientsListing, {
			props: {
				clients: [
					client({
						dob: null,
						address: { line1: '1 Meneage St', city: 'Helston', postcode: 'TR13 9PU', country: 'GB' }
					})
				],
				links
			}
		}).body;
		expect(located).toContain('Helston, TR13 9PU, GB');
		expect(located).not.toContain('Meneage');

		const bare = render(ClientsListing, {
			props: { clients: [client({ dob: null, address: null })], links }
		}).body;
		expect(bare).toContain('Theo Caldwell');
		expect(bare).not.toContain('Helston');
	});

	it('prints growth under the value while the growth column is hidden', () => {
		// The column drops out below a `5xl` table; the figure must not vanish with it. It is drawn
		// under the value with the inverse class, so the two are never both on screen.
		const body = render(ClientsListing, {
			props: { clients: [client({ portfolio_value: 125_000, growth: 8_000 })], links }
		}).body;
		expect(body).toContain('hidden @5xl:table-cell');
		expect(body).toContain('block @5xl:hidden');
		expect(body).toContain('+£8,000');
	});

	it('says nothing rather than zero when a client holds no accounts', () => {
		const body = render(ClientsListing, {
			props: { clients: [client({ account_types: [], account_count: 0 })], links }
		}).body;
		expect(body).not.toContain('>GIA<');
	});

	it('reads the status badge and the wrapper tags from the left', () => {
		// The Accounts column was a CENTRED single digit, and centring is what put a gap between it
		// and Adviser's avatar (right-aligned, the two sat on the same boundary from opposite sides —
		// which is why that gap was "fixed" twice with no effect). It is a row of tags now, and a
		// list reads from the left like every other list in the product; the tags' own width keeps
		// them off the boundary, since padding here is forbidden for the reason the guard below
		// records. Status is a badge, so it reads from the left too.
		const body = render(ClientsListing, { props: { clients: [client()], links } }).body;
		expect(body).not.toContain('text-center');
		// The manufactured gutter and its 1rem predecessor are both still gone.
		expect(body).not.toContain('pl-16');
		expect(body).not.toMatch(/pl-4[^0-9]/);
	});

	it('never declares `align: right` on a column whose cell is a fixed-width thing', () => {
		// `align: 'right'` pins such a cell to the edge so the row cannot give the column its space
		// back, and the table scrolls with the panel half empty — fixed three times elsewhere
		// (apps/admin switch-columns.test.ts). Source scan, comments stripped, because nothing about
		// it is a type error and the table still renders.
		const source = columnSource();
		const strip = (t: string) => t.replace(/\/\/.*/g, '');
		const accounts = strip(
			source.slice(source.indexOf("key: 'accounts'"), source.indexOf("key: 'adviser'"))
		);
		// Bounded to `baseColumns`. Reading to end-of-file swallowed the archived-view `restore`
		// column, which is right-aligned for a good reason (it holds a button at the row's end), so
		// the guard failed on a column it has no opinion about.
		const statusAt = source.indexOf("key: 'status'");
		const status = strip(source.slice(statusAt, source.indexOf('];', statusAt)));
		// Both read from the left: Accounts holds a list, Status a badge.
		expect(accounts).not.toContain("align: 'right'");
		expect(status).not.toContain("align: 'center'");
		expect(status).not.toContain("align: 'right'");
	});

	it('sizes every known-size column in rem and leaves Client the remainder', () => {
		// `table-fixed` hands out exactly what the headers declare. Currency (one flag pill), Status
		// (one badge, "Suspended" at its widest) and the three money columns (a figure of at most
		// eight digits) are a KNOWN size, so a share gave them too much on a wide screen and too
		// little on a narrow one — the badge clipped, a seven-figure value spilled into the pill
		// beside it. They are rem now, and the money columns at least wide enough for
		// "£12,345,678.90" (~120px + padding). Accounts (three tags and a count) and Adviser (a face,
		// "Diane J.", a count) are bounded too and step up with the table — a 12% share clipped even
		// the short adviser name at 716px. Client is the ONE column with no width: the auto column
		// takes what the others leave, which is what the column holding the sentences should do, and
		// nothing else may be auto, or Client's remainder is split with it. Read from source: the
		// widths are authored, not rendered.
		const source = columnSource().replace(/\/\/.*/g, '');
		const columns = source.split(/\n\t\t\{\n/).filter((chunk) => /key: '/.test(chunk));
		const widthOf = (chunk: string) => /headerClass: 'w-\[([^\]']+)\]/.exec(chunk)?.[1] ?? null;
		const byKey = Object.fromEntries(
			columns.map((chunk) => [/key: '(\w+)'/.exec(chunk)![1], widthOf(chunk)])
		);
		expect(byKey.name).toBeNull();
		for (const key of ['accounts', 'adviser', 'currency', 'status']) {
			expect(byKey[key], key).toMatch(/rem$/);
		}
		for (const key of ['value', 'cash', 'growth']) {
			expect(byKey[key], key).toMatch(/rem$/);
			expect(Number.parseFloat(byKey[key]!), key).toBeGreaterThanOrEqual(8);
		}
		expect(source).toMatch(/headerClass: 'w-\[7rem\] @4xl:w-\[10rem\]'/); // accounts
		expect(source).toMatch(/headerClass: 'w-\[7\.5rem\] @4xl:w-\[10rem\]'/); // adviser
		expect(source).not.toMatch(/headerClass: 'w-\[\d+%\]'/);
	});

	it('drops Adviser, Cash and Growth by the TABLE’s width, on the dashboard’s rungs', () => {
		// The listing is also mounted beside rails and in admin's operate-as-firm pane, where the
		// viewport says nothing about the room the table has — and a money column that stays at a
		// width that cannot hold its figure spills rather than hides. Container rungs, the same as
		// `BOOK_COLUMNS`: this IS that book.
		const body = render(ClientsListing, { props: { clients: [client()], links } }).body;
		expect(body).toContain('hidden @2xl:table-cell');
		expect(body).toContain('hidden @4xl:table-cell');
		expect(body).toContain('hidden @5xl:table-cell');
		expect(body).not.toMatch(/hidden (md|lg):table-cell/);
	});

	it('keeps horizontal padding OFF the narrow column, or the table overflows sideways', () => {
		// The trap in the first attempt at spacing this pair, which was worse than the gap it fixed. Under
		// `table-fixed` a column still cannot render narrower than its own unbreakable content, and
		// every head is `whitespace-nowrap` — so 40px of `pr` on Accounts puts its minimum near 110px,
		// which 10% of a ~900px content pane does not cover. The declared shares then stop being
		// honourable and the whole table overflows horizontally. Adviser is more than twice as wide and
		// truncates, so the same padding there costs a few characters of a name and nothing else.
		const source = columnSource();
		// Comments STRIPPED before matching. The comment on that column explains the rule by naming the
		// very class it forbids, so a raw scan flags the explanation as the defect — the same false
		// positive `switch-columns.test.ts` and `field-shell.test.ts` both record hitting, and the
		// reason both strip first.
		const accounts = source
			.slice(source.indexOf("key: 'accounts'"), source.indexOf("key: 'adviser'"))
			.replace(/\/\/.*/g, '');
		expect(accounts).toMatch(/w-\[[\d.]+rem\]/); // it still declares a width
		// No `\b` in this pattern on purpose: a heredoc turned one into a literal backspace
		// byte here twice, and a regex that cannot match makes `not.toMatch` pass for ever. `px-`
		// and `pr-` are unambiguous on their own.
		expect(accounts).not.toMatch(/p[xr]-\d/);
	});
});
