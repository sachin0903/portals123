import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Pagination from './pagination.svelte';

/**
 * SSR pins for the pager's contract: page NUMBERS only (no first/prev/next/last arrows), the
 * current page marked for assistive tech, the gap a NAMED control, and every control carrying
 * the coarse-pointer touch size.
 *
 * The arrows were removed deliberately. `items` always renders the first and last page, so
 * "first" and "last" duplicated a control already on screen, and prev/next duplicated a
 * neighbouring number — four extra targets for no extra reach. Their disabled-state
 * accessibility (the previous version of this test) is moot once they do not exist.
 *
 * What removing them left behind is reach into the MIDDLE of a long set: with 24 pages, page 12
 * sits behind a gap. So the gap stopped being a decorative `aria-hidden` span and became a
 * control that opens a "go to page" field. The pins below are the two halves of that being an
 * improvement rather than a trap — it has an accessible name, and the name states where it goes.
 * A control that looks clickable must not be invisible to assistive tech, and must not offer a
 * click whose destination is unknowable.
 */

describe('Pagination (SSR, href mode)', () => {
	const href = (n: number) => `?page=${n}`;

	it('renders page numbers only — no directional controls', () => {
		const { body } = render(Pagination, { props: { page: 1, pageCount: 3, href } });
		for (const label of ['First page', 'Previous page', 'Next page', 'Last page']) {
			expect(body).not.toContain(`aria-label="${label}"`);
		}
		// Every page is reachable directly, which is what replaces them.
		expect(body).toContain('aria-label="Page 1"');
		expect(body).toContain('href="?page=2"');
		expect(body).toContain('href="?page=3"');
		// 3 pages means no gap, so nothing decorative should appear.
		expect(body).not.toContain('aria-hidden="true"');
	});

	it('marks the current page for assistive tech', () => {
		const { body } = render(Pagination, { props: { page: 2, pageCount: 5, href } });
		expect(body).toContain('aria-current="page"');
	});

	it('always offers the first and last page, with each gap as a named control', () => {
		const { body } = render(Pagination, { props: { page: 10, pageCount: 20, href } });
		// The ends are what make a 20-page set navigable without arrows.
		expect(body).toContain('href="?page=1"');
		expect(body).toContain('href="?page=20"');
		// The gap keeps its glyph, but as a button — never a decorative span again.
		expect(body).toContain('…');
		expect(body).not.toContain('aria-hidden="true">…<');
	});

	it('names each gap with the range it hides, rather than offering a blind click', () => {
		// 1 … 9 [10] 11 … 20 — the two gaps hide 2–8 and 12–19.
		const { body } = render(Pagination, { props: { page: 10, pageCount: 20, href } });
		expect(body).toContain('aria-label="Go to a page between 2 and 8"');
		expect(body).toContain('aria-label="Go to a page between 12 and 19"');
	});

	it('the gap is reachable by assistive tech and by the keyboard', () => {
		const { body } = render(Pagination, { props: { page: 2, pageCount: 24, href } });
		// A `<button>`, so it is tabbable and announced; the old span was neither.
		expect(body).toMatch(/<button[^>]*aria-label="Go to a page between 4 and 23"/);
	});

	it('renders no gap control when every page fits', () => {
		const { body } = render(Pagination, { props: { page: 1, pageCount: 3, href } });
		expect(body).not.toContain('…');
		expect(body).not.toContain('Go to a page between');
	});

	it('is ghost-styled: no border or filled background on the controls', () => {
		const { body } = render(Pagination, { props: { page: 1, pageCount: 3, href } });
		expect(body).not.toContain('border-primary');
		expect(body).not.toContain('bg-primary');
	});

	it('controls carry the coarse-pointer touch size without changing desktop density', () => {
		const { body } = render(Pagination, { props: { page: 2, pageCount: 3, href } });
		expect(body).toContain('size-8');
		expect(body).toContain('pointer-coarse:size-10');
	});
});
