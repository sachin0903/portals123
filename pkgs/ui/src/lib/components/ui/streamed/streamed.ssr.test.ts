import { describe, expect, it } from 'vitest';
import { createRawSnippet } from 'svelte';
import { render } from 'svelte/server';
import Streamed from './streamed.svelte';
import ListingSkeleton from '../listing-skeleton/listing-skeleton.svelte';
import RecordSkeleton from '../record-skeleton/record-skeleton.svelte';

/**
 * The server half of streamed page data.
 *
 * What matters here is which branch the SERVER emits, because that is the frame a user sees on the
 * click: a page whose loader streams its rows must render its placeholder, and one whose data is
 * already in hand must render the content (a page that stops streaming must not gain a skeleton
 * flash). The keep-the-last-answer rule needs a mounted component and lives in the dom test.
 */
const pending = createRawSnippet(() => ({ render: () => '<p>placeholder</p>' }));
// `render()` erases a component's generic parameters, so `T` is `unknown` at this call site and the
// snippet has to declare that and narrow inside — the same shape `nullable` uses below.
const children = createRawSnippet((book: () => unknown) => ({
	render: () => `<p>${(book() as { rows: number }).rows} rows</p>`
}));

describe('Streamed (SSR)', () => {
	it('renders the placeholder for a promise — the frame the click buys', () => {
		const body = render(Streamed, {
			props: { data: new Promise<{ rows: number }>(() => {}), pending, children }
		}).body;
		expect(body).toContain('placeholder');
		expect(body).not.toContain('rows');
	});

	it('renders the content for a value that is already in hand', () => {
		const body = render(Streamed, { props: { data: { rows: 12 }, pending, children } }).body;
		expect(body).toContain('12 rows');
		expect(body).not.toContain('placeholder');
	});

	it('treats null as an answer, not as "nothing yet"', () => {
		// The value is boxed inside the component for exactly this: a loader may legitimately stream
		// `null`, and rendering the placeholder for ever would be the wrong reading of it.
		const nullable = createRawSnippet((value: () => unknown) => ({
			render: () => `<p>${value() === null ? 'no book' : 'a book'}</p>`
		}));
		const body = render(Streamed, {
			props: { data: null, pending, children: nullable }
		}).body;
		expect(body).toContain('no book');
	});
});

describe('ListingSkeleton (SSR)', () => {
	it('draws the listing’s own box — toolbar, rows and pager, not a spinner', () => {
		const body = render(ListingSkeleton, {
			props: { rows: 3, filters: 2, fill: false, pager: false, columns: 3 }
		}).body;
		// One status region for the whole placeholder: the bars are decorative, so a screen reader is
		// told "Loading" once rather than once per bar.
		expect(body.match(/role="status"/g)).toHaveLength(1);
		expect(body).toContain('aria-label="Loading"');
		// Search + Filter (one funnel, not N chips) + 3 header cells + 3 rows. No page heading —
		// staff listings dropped those (the topbar crumb is the title).
		expect(body.match(/data-slot="skeleton"/g)).toHaveLength(2 + 3 + 3);
		expect(body).toContain('h-9 w-full sm:w-96');
		expect(body).not.toContain('max-w-sm');
	});

	it('fills the viewport the way the book listings do', () => {
		const body = render(ListingSkeleton, { props: { rows: 1, filters: 0, pager: false } }).body;
		expect(body).toContain('md:h-[calc(100svh-var(--topbar-h,4rem))]');
		const inset = render(ListingSkeleton, {
			props: { rows: 1, filters: 0, fill: false, pager: false }
		}).body;
		expect(inset).not.toContain('md:h-[calc(100svh-var(--topbar-h,4rem))]');
	});

	it('draws the two-line spine row, at that row’s height, when asked', () => {
		// The clients book stacks the date of birth under the name, so its rows are `h-13`; a
		// placeholder at the plain `h-11` lands every row eight pixels short and the page moves
		// when the real ones arrive. Avatar + two bars per row, against one bar at `h-11`.
		const plain = render(ListingSkeleton, {
			props: { rows: 2, filters: 0, fill: false, pager: false, columns: 0 }
		}).body;
		const spine = render(ListingSkeleton, {
			props: { rows: 2, filters: 0, fill: false, pager: false, columns: 0, spine: true }
		}).body;
		expect(plain).toContain('h-11');
		expect(plain).not.toContain('h-13');
		expect(spine).toContain('h-13');
		expect(spine).not.toContain('h-11');
		// Search + (avatar + two lines) × 2 rows, against search + one bar × 2 rows.
		expect(spine.match(/data-slot="skeleton"/g)).toHaveLength(1 + 3 * 2);
		expect(plain.match(/data-slot="skeleton"/g)).toHaveLength(1 + 1 * 2);
	});

	it('can still draw a heading when a surface that has one asks', () => {
		const withHeading = render(ListingSkeleton, {
			props: { rows: 1, filters: 0, header: true, fill: false, pager: false, columns: 0 }
		}).body;
		const without = render(ListingSkeleton, {
			props: { rows: 1, filters: 0, fill: false, pager: false, columns: 0 }
		}).body;
		expect(withHeading.match(/data-slot="skeleton"/g)).toHaveLength(4);
		expect(without.match(/data-slot="skeleton"/g)).toHaveLength(2);
	});
});

describe('RecordSkeleton (SSR)', () => {
	it('draws a header and cards — the record’s own box, not a spinner', () => {
		const body = render(RecordSkeleton, { props: { cards: 1, rows: 2, kpis: 0 } }).body;
		expect(body.match(/role="status"/g)).toHaveLength(1);
		expect(body).toContain('aria-label="Loading"');
		// Heading + description + card title + two rows.
		expect(body.match(/data-slot="skeleton"/g)).toHaveLength(5);
	});

	it('can draw KPI tiles for an overview', () => {
		const body = render(RecordSkeleton, { props: { kpis: 4, cards: 0, header: false } }).body;
		expect(body.match(/data-slot="skeleton"/g)).toHaveLength(8);
	});
});
