/**
 * The library's row selection MOUNTS, because the bug it exists for is only visible on a click.
 *
 * THE DEFECT. Both checkboxes were driven by a bare `onclick`. bits-ui merges a caller's `onclick`
 * with its own through `composeHandlers`, which runs ours and then its own `#toggle()` — so every
 * click toggled twice: our `selected` update, then bits-ui writing the OPPOSITE value into the
 * `checked` prop. That prop is `$bindable` and passed unbound, so bits-ui's write shadows the
 * parent's value and the box renders inverted until something else forces a change. Select-all
 * ticked when nothing was selected and cleared when everything was.
 *
 * An SSR test cannot see this: the server renders the initial `checked` correctly and never runs a
 * handler. Per CLAUDE.md, `setTimeout` stays REAL (faking it kills vitest's worker heartbeat and the
 * run dies as a hang) and the import gets a generous timeout, because pulling bits-ui through Vite's
 * cold transform beats the 10s default on Windows.
 */
import { describe, expect, it } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import DocumentsLibrary from './documents-library.svelte';
import type { LibraryRow } from './documents.js';

const links = {
	document: (uid: string) => `/documents/${uid}`,
	documentContent: (uid: string) => `/documents/${uid}/content`,
	fileDownload: (uid: string) => `/files/${uid}/download`,
	requests: '/documents/requests'
};

function row(over: Partial<LibraryRow> = {}): LibraryRow {
	return {
		uid: 'doc1',
		name: 'Statement.pdf',
		category: 'statement',
		documentType: 'pdf',
		source: 'uploaded',
		status: 'active',
		scope: 'firm',
		sizeBytes: 1024,
		createdAt: '2026-01-01T10:00:00Z',
		createdBy: 'Bo Adviser',
		client: 'Margaret Okonjo',
		error: null,
		...over
	} as LibraryRow;
}

function mounted(documents: LibraryRow[]) {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(DocumentsLibrary, {
		target,
		props: { documents, links, actions: { archive: async () => {} } }
	});
	return { target, dispose: () => unmount(component) };
}

/** The header "Select all" box, found by its accessible name rather than by position. */
const selectAll = (target: HTMLElement): HTMLElement =>
	target.querySelector<HTMLElement>('th [aria-label="Select all"]')!;

/** A folder band's own box, by the band's label. */
const bandBox = (target: HTMLElement, label: string): HTMLElement =>
	target.querySelector<HTMLElement>(`[aria-label="Select all in ${label}"]`)!;

const rowBoxes = (target: HTMLElement): HTMLElement[] =>
	[...target.querySelectorAll<HTMLElement>('[aria-label^="Select "]')].filter(
		(b) => !b.getAttribute('aria-label')?.startsWith('Select all')
	);

/** Band disclosures; `td[colspan]` excludes the row kebab, which also has `aria-expanded`. */
const bands = (target: HTMLElement): HTMLElement[] => [
	...target.querySelectorAll<HTMLElement>('td[colspan] button[aria-expanded]')
];

const state = (el: HTMLElement | undefined): string | null | undefined =>
	el?.getAttribute('data-state');

/** Open every band, one per pass: opening one re-renders the body. */
async function showRows(target: HTMLElement): Promise<void> {
	await tick();
	for (let pass = 0; pass < 40; pass++) {
		const shut = bands(target).find((b) => b.getAttribute('aria-expanded') === 'false');
		if (!shut) return;
		shut.click();
		await tick();
	}
}

/** `cancelable`, or the box's `preventDefault` does nothing and the double toggle returns. */
const shiftClick = (el: HTMLElement): void => {
	el.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, shiftKey: true }));
};

const sortBy = (target: HTMLElement, header: string): void =>
	[...target.querySelectorAll<HTMLElement>('th button')]
		.find((b) => b.textContent?.trim() === header)!
		.click();

describe('the search box in LOCAL mode (no listing/onquery injected)', () => {
	/**
	 * THE DEFECT. `ListingToolbar` takes `query` as a `$bindable` prop and writes the typed term
	 * into it; without an `onsearch` it then stops. The library passed `query={term}` as an
	 * EXPRESSION rather than a binding, so that write never reached the parent's `search` state and
	 * typing in the box filtered nothing at all. It replaced an `<Input bind:value={search}>` that
	 * worked, and only the server-driven mount exercised the other path — so the documented
	 * "a mount that has not adopted the query" fallback was silently dead.
	 *
	 * SSR cannot see this: the server renders the box and never runs an input handler.
	 */
	it('narrows the list as you type', async () => {
		const { target, dispose } = mounted([
			row({ uid: 'a', name: 'Annual review.pdf' }),
			row({ uid: 'b', name: 'Passport.pdf' })
		]);
		await showRows(target);
		expect(target.textContent).toContain('Annual review');
		expect(target.textContent).toContain('Passport');

		const box = target.querySelector<HTMLInputElement>('input[type="search"]')!;
		box.value = 'annual';
		box.dispatchEvent(new Event('input', { bubbles: true }));
		// `onsearch` is debounced (300ms) — real timers, per the note at the top of this file.
		await new Promise((resolve) => setTimeout(resolve, 400));
		await tick();

		expect(target.textContent).toContain('Annual review');
		expect(target.textContent).not.toContain('Passport');
		dispose();
	});
});

describe('select-all', () => {
	it('selects what is on screen, then clears it — and the tick tracks the selection both times', async () => {
		const { target, dispose } = mounted([row({ uid: 'a' }), row({ uid: 'b', name: 'B.pdf' })]);
		await showRows(target);
		const box = selectAll(target);
		expect(box.getAttribute('data-state')).toBe('unchecked');

		box.click();
		await tick();
		// The bulk bar is the honest read of `selected`; `data-state` is the read of the box. Before
		// the fix these disagreed — which is exactly what "it inverts" looked like.
		expect(target.textContent).toContain('2 selected');
		expect(box.getAttribute('data-state')).toBe('checked');

		box.click();
		await tick();
		expect(target.textContent).not.toContain('selected');
		expect(box.getAttribute('data-state')).toBe('unchecked');
		dispose();
	});

	it('shows a dash, not an empty box, when only some of the list is selected', async () => {
		const { target, dispose } = mounted([row({ uid: 'a' }), row({ uid: 'b', name: 'B.pdf' })]);
		await showRows(target);
		rowBoxes(target)[0]!.click();
		await tick();
		expect(target.textContent).toContain('1 selected');
		// `indeterminate` — a half-selected list used to be indistinguishable from an empty one.
		expect(selectAll(target).getAttribute('data-state')).toBe('indeterminate');
		dispose();
	});
});

describe('a folder’s select box', () => {
	const book = () => [
		row({ uid: 'a', name: 'Statement.pdf', client: 'Margaret Okonjo' }),
		row({
			uid: 'b',
			name: 'Passport.pdf',
			category: 'passport',
			isFile: true,
			client: 'Margaret Okonjo'
		}),
		row({ uid: 'c', name: 'Report.pdf', client: 'Nkechi Bello' })
	];

	it('select-all and a folder box both reach into SHUT folders, dashing when partial', async () => {
		const { target, dispose } = mounted(book());
		await tick();
		expect(rowBoxes(target)).toHaveLength(0);
		// Select-all speaks for the whole filtered set, shut bands included.
		selectAll(target).click();
		await tick();
		expect(target.textContent).toContain('3 selected');
		expect(state(bandBox(target, 'Margaret Okonjo'))).toBe('checked');
		expect(state(bandBox(target, 'Nkechi Bello'))).toBe('checked');
		selectAll(target).click();
		await tick();

		bandBox(target, 'Margaret Okonjo').click();
		await tick();
		expect(target.textContent).toContain('2 selected');
		expect(state(bandBox(target, 'Margaret Okonjo'))).toBe('checked');
		expect(state(bandBox(target, 'Nkechi Bello'))).toBe('unchecked');
		expect(state(selectAll(target))).toBe('indeterminate');
		bandBox(target, 'Margaret Okonjo').click();
		await tick();
		expect(target.textContent).not.toContain('selected');

		await showRows(target);
		rowBoxes(target)
			.find((b) => b.getAttribute('aria-label') === 'Select Statement.pdf')!
			.click();
		await tick();
		expect(state(bandBox(target, 'Margaret Okonjo'))).toBe('indeterminate');
		expect(state(bandBox(target, 'Statement'))).toBe('checked');
		expect(state(bandBox(target, 'Passport'))).toBe('unchecked');
		// A partial folder's box completes it rather than clearing it.
		bandBox(target, 'Margaret Okonjo').click();
		await tick();
		expect(state(bandBox(target, 'Margaret Okonjo'))).toBe('checked');
		expect(target.textContent).toContain('2 selected');
		dispose();
	});
});

describe('a document the firm may not delete', () => {
	it('is selectable, but the bulk Delete does not count it', async () => {
		// The bar's count IS its delete target. Counting a protected row would promise "Delete 2"
		// and delete one, with the other's 409 either swallowed or failing the whole batch.
		const { target, dispose } = mounted([
			row({ uid: 'a' }),
			row({ uid: 'b', name: 'Synced.pdf', deletable: false })
		]);
		await showRows(target);
		selectAll(target).click();
		await tick();
		expect(target.textContent).toContain('2 selected');
		expect(target.textContent).toContain('Delete 1');
		expect(target.textContent).not.toContain('Delete 2');
		dispose();
	});

	it('offers no bulk Delete at all when only protected rows are selected', async () => {
		const { target, dispose } = mounted([row({ uid: 'b', name: 'Synced.pdf', deletable: false })]);
		await showRows(target);
		selectAll(target).click();
		await tick();
		expect(target.textContent).toContain('1 selected');
		expect(target.textContent).not.toContain('Delete');
		dispose();
	});
});

describe('a row checkbox', () => {
	it('ticks on the first click and clears on the second', async () => {
		const { target, dispose } = mounted([row({ uid: 'a' })]);
		await showRows(target);
		const box = rowBoxes(target)[0]!;
		expect(box.getAttribute('data-state')).toBe('unchecked');

		box.click();
		await tick();
		expect(box.getAttribute('data-state')).toBe('checked');
		expect(target.textContent).toContain('1 selected');

		box.click();
		await tick();
		expect(box.getAttribute('data-state')).toBe('unchecked');
		expect(target.textContent).not.toContain('selected');
		dispose();
	});

	// Charlie newest, so Created-descending renders it first.
	const sortableBook = () => [
		row({ uid: 'c', name: 'Charlie.pdf', createdAt: '2026-01-03T10:00:00Z' }),
		row({ uid: 'a', name: 'Alpha.pdf', createdAt: '2026-01-02T10:00:00Z' }),
		row({ uid: 'b', name: 'Bravo.pdf', createdAt: '2026-01-01T10:00:00Z' })
	];

	it('shift-clicks a range along the RENDERED order', async () => {
		const a = mounted(sortableBook());
		await showRows(a.target);
		sortBy(a.target, 'Document');
		await tick();
		const boxes = rowBoxes(a.target);
		expect(boxes.map((b) => b.getAttribute('aria-label'))).toEqual([
			'Select Alpha.pdf',
			'Select Bravo.pdf',
			'Select Charlie.pdf'
		]);
		boxes[0]!.click();
		await tick();
		shiftClick(rowBoxes(a.target)[1]!);
		await tick();
		// Alpha→Bravo along this rendered order — not Charlie, which only arrived first.
		expect(rowBoxes(a.target).map((b) => state(b))).toEqual(['checked', 'checked', 'unchecked']);
		a.dispose();
	});
});

describe('folder bands', () => {
	it('groups by client then type, shut; opens onto type bands, with Type still a filter', async () => {
		const { target, dispose } = mounted([
			row({ uid: 'a', name: 'Statement.pdf', client: 'Margaret Okonjo' }),
			row({
				uid: 'b',
				name: 'Passport.pdf',
				category: 'passport',
				isFile: true,
				client: 'Margaret Okonjo'
			}),
			row({ uid: 'c', name: 'Report.pdf', client: 'Nkechi Bello' }),
			row({ uid: 'd', name: 'Orphan.pdf', client: '' })
		]);
		await tick();

		expect(bands(target).map((b) => b.textContent?.trim().replace(/\s+/g, ' '))).toEqual([
			'Margaret Okonjo 2',
			'Nkechi Bello 1',
			'No client 1'
		]);
		expect(rowBoxes(target)).toHaveLength(0);

		// A client opens onto its type bands, already open.
		bands(target)[0]!.click();
		await tick();
		const types = () =>
			bands(target)
				.filter((b) => b.closest('tr')?.getAttribute('aria-level') === '2')
				.map((b) => b.textContent?.trim().replace(/\s+\d+$/, ''));
		expect(types()).toEqual(['Passport', 'Statement']);
		expect(rowBoxes(target)).toHaveLength(2);

		// A band is not a filter: Type still narrows the set on top of them.
		expect(target.textContent).toContain('4 of 4');
		const typeButton = [...target.querySelectorAll<HTMLElement>('td button')].find(
			(b) => b.textContent?.trim() === 'Passport'
		);
		typeButton!.click();
		await tick();
		expect(target.textContent).toContain('1 of 4');
		dispose();
	});
});
