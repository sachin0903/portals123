/**
 * @vitest-environment jsdom
 *
 * The rules this file exists for cannot be seen from an SSR render: what a streamed listing does
 * with the answer it is showing when the NEXT promise arrives.
 *
 * These listings are server-driven, so every keystroke in the search box and every page cut is a
 * fresh navigation with a fresh promise — and the two want opposite things. A page cut asked for
 * different rows, so the placeholder comes back until they land (a table that sits unchanged after
 * the click reads as a pager that does not work). A keystroke must not do that: swapping the listing
 * out takes the search box with it, and the focus and caret in the box being typed into. The
 * component tells them apart by where focus is, which only a mounted component can be asked.
 */
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { Component, Snippet } from 'svelte';
import { bookSnippet, reactiveProps } from './streamed.harness.svelte.js';

type Book = { label: string };
type Props = {
	data: Promise<Book> | Book;
	pending: Snippet;
	children: Snippet<[Book, boolean]>;
	keep?: boolean;
};

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let createRawSnippet: typeof import('svelte').createRawSnippet;
let Streamed: Component<Props>;

let pending: Snippet;
let children: Snippet<[Book, boolean]>;

beforeAll(async () => {
	({ mount, unmount, flushSync, createRawSnippet } = await import('svelte'));
	Streamed = (await import('./streamed.svelte')).default as unknown as typeof Streamed;
	pending = createRawSnippet(() => ({ render: () => '<p data-testid="pending">loading</p>' }));
	// A raw snippet renders its markup once and then patches the node, which is what makes the
	// identity assertions below meaningful: if the content were remounted for the second promise,
	// this node would be a different element.
	children = bookSnippet<Book>();
	// Generous: a cold vite transform of the component graph beats the 10s default on Windows.
}, 120_000);

afterEach(() => {
	document.body.innerHTML = '';
});

/** Mount with a controllable promise; `settle` resolves it and flushes the render. */
function mountStreamed(first: Book, extra: Partial<Props> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	let resolve!: (book: Book) => void;
	const data = new Promise<Book>((r) => (resolve = r));
	const props = reactiveProps<Props>({ data, pending, children, ...extra });
	const app = mount(Streamed, { target, props });
	flushSync();
	return {
		target,
		app,
		props,
		settle: async (book: Book = first) => {
			resolve(book);
			await Promise.resolve();
			flushSync();
		}
	};
}

describe('Streamed', () => {
	it('shows the placeholder until the first answer, then the content', async () => {
		const { target, app, settle } = mountStreamed({ label: 'first' });
		expect(target.querySelector('[data-testid="pending"]')).not.toBeNull();

		await settle();
		expect(target.querySelector('[data-testid="pending"]')).toBeNull();
		expect(target.textContent).toContain('first');

		unmount(app);
	});

	it('goes back to the placeholder when a page cut asks for different rows', async () => {
		const { target, app, props, settle } = mountStreamed({ label: 'page one' });
		await settle();
		expect(target.textContent).toContain('page one');
		let resolveNext!: (book: Book) => void;
		// What a pager click does: focus has left any text field (it is on the link, or on the body
		// in Safari) and a new promise arrives for a different page.
		(document.activeElement as HTMLElement | null)?.blur?.();
		props.data = new Promise<Book>((r) => (resolveNext = r));
		flushSync();

		expect(target.querySelector('[data-testid="pending"]'), 'the skeleton is back').not.toBeNull();
		expect(target.textContent).not.toContain('page one');

		resolveNext({ label: 'page two' });
		await Promise.resolve();
		flushSync();
		expect(target.querySelector('[data-testid="pending"]')).toBeNull();
		expect(target.textContent).toContain('page two');

		unmount(app);
	});

	it('holds the answer on screen while a text field has focus — the search box being typed into', async () => {
		const { target, app, props, settle } = mountStreamed({ label: 'first' });
		await settle();
		const shown = target.querySelector('[data-testid="book"]');

		// The search box, outside or inside the streamed content — either way it must survive.
		const search = document.createElement('input');
		search.type = 'search';
		document.body.append(search);
		search.focus();
		expect(document.activeElement).toBe(search);

		// What a filter keystroke does: a new promise for the same listing.
		let resolveNext!: (book: Book) => void;
		props.data = new Promise<Book>((r) => (resolveNext = r));
		flushSync();

		expect(
			target.querySelector('[data-testid="pending"]'),
			'never back to the skeleton'
		).toBeNull();
		expect(target.querySelector('[data-testid="book"]'), 'the same element, not a remount').toBe(
			shown
		);
		expect(shown?.getAttribute('data-stale'), 'a typing hold is not reported as stale').toBe(
			'false'
		);

		resolveNext({ label: 'second' });
		await Promise.resolve();
		flushSync();
		expect(target.querySelector('[data-testid="book"]'), 'updated in place').toBe(shown);
		expect(target.textContent).toContain('second');

		unmount(app);
	});

	it('`keep` holds the answer regardless and says it is stale until the next one lands', async () => {
		const { target, app, props, settle } = mountStreamed({ label: 'first' }, { keep: true });
		await settle();
		const shown = target.querySelector('[data-testid="book"]');
		(document.activeElement as HTMLElement | null)?.blur?.();

		let resolveNext!: (book: Book) => void;
		props.data = new Promise<Book>((r) => (resolveNext = r));
		flushSync();

		expect(target.querySelector('[data-testid="pending"]')).toBeNull();
		expect(target.querySelector('[data-testid="book"]')).toBe(shown);
		expect(shown?.getAttribute('data-stale')).toBe('true');

		resolveNext({ label: 'second' });
		await Promise.resolve();
		flushSync();
		expect(shown?.getAttribute('data-stale')).toBe('false');
		expect(target.textContent).toContain('second');

		unmount(app);
	});

	it('reports a failure only when there is nothing to show instead', async () => {
		const target = document.createElement('div');
		document.body.append(target);
		const props = reactiveProps<Props>({
			data: Promise.reject(new Error('boom')),
			pending,
			children
		});
		const app = mount(Streamed, { target, props });
		flushSync();
		await Promise.resolve();
		await Promise.resolve();
		flushSync();
		// The default banner: the loaders' own `loadError` flag is the normal report, so this is for a
		// promise that rejected outright.
		expect(target.querySelector('[role="alert"]')).not.toBeNull();
		unmount(app);
	});

	it('a stale answer settling late does not overwrite the current one', async () => {
		// Two navigations in flight: the first must not win by arriving second.
		const target = document.createElement('div');
		document.body.append(target);
		let resolveOld!: (book: Book) => void;
		let resolveNew!: (book: Book) => void;
		const props = reactiveProps<Props>({
			data: new Promise<Book>((r) => (resolveOld = r)),
			pending,
			children
		});
		const app = mount(Streamed, { target, props });
		flushSync();

		props.data = new Promise<Book>((r) => (resolveNew = r));
		flushSync();
		resolveNew({ label: 'current' });
		await Promise.resolve();
		flushSync();
		resolveOld({ label: 'stale' });
		await Promise.resolve();
		flushSync();

		expect(target.textContent).toContain('current');
		expect(target.textContent).not.toContain('stale');
		unmount(app);
	});
});
