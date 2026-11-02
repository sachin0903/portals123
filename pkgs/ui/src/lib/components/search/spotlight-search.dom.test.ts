/**
 * The shared palette, mounted in jsdom: the dialog is portalled and client-only, so a server
 * render carries none of it. What is pinned here is the simplified contract — a field, no quick
 * actions, prefixes, guide or keycaps — and the two ways it opens: the app's `open` binding, and
 * the `openSearchPalette(scope)` window event the browse bar's search button fires, which must
 * land it already narrowed to that category with a way back to all. The category row is always
 * on show — every scope, "All" first, a pressed chip toggling off — and the phone's round close is
 * its own control, not the dialog's close (which the shared panel class hides below `sm`).
 */
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { Component } from 'svelte';
import SearchIcon from '@lucide/svelte/icons/search';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let SpotlightSearch: Component<Record<string, unknown>>;
let SearchClient: typeof import('../../search.js').SearchClient;
let openSearchPalette: typeof import('../../search.js').openSearchPalette;

beforeAll(async () => {
	// jsdom has no scrollIntoView. bits-ui calls it when results land, and the
	// rejection fails the run after the assertion has already passed.
	Element.prototype.scrollIntoView = () => {};
	Object.defineProperty(window, 'matchMedia', {
		writable: true,
		value: (query: string) => ({
			matches: false,
			media: query,
			onchange: null,
			addEventListener: () => {},
			removeEventListener: () => {},
			addListener: () => {},
			removeListener: () => {},
			dispatchEvent: () => false
		})
	});
	({ mount, unmount, flushSync } = await import('svelte'));
	SpotlightSearch = (await import('./spotlight-search.svelte')).default as unknown as Component<
		Record<string, unknown>
	>;
	({ SearchClient, openSearchPalette } = await import('../../search.js'));
}, 180_000);

const mounted: ReturnType<typeof mount>[] = [];
afterEach(() => {
	for (const c of mounted.splice(0)) unmount(c);
	document.body.innerHTML = '';
});

const show = (props: Record<string, unknown> = {}) => {
	const target = document.body.appendChild(document.createElement('div'));
	const component = mount(SpotlightSearch, {
		target,
		props: {
			scopes: [
				{ type: 'document', label: 'Documents' },
				{ type: 'insight', label: 'Insights' }
			],
			icons: { document: SearchIcon },
			typeLabels: { document: 'Document' },
			routes: { document: (id: string) => `/documents/${id}` },
			client: new SearchClient(
				(q: string) => `/search?q=${q}`,
				async () => new Response('{"query":"","groups":[],"truncated":false}')
			),
			onnavigate: () => {},
			placeholder: 'Search your financial world…',
			...props
		}
	});
	mounted.push(component);
	flushSync();
	return component;
};

const field = () => document.querySelector<HTMLInputElement>('[data-slot=command-input]');
const chips = () =>
	[
		...document.querySelectorAll<HTMLButtonElement>(
			'[aria-label="Filter search results by category"] button'
		)
	].map((b) => `${b.textContent?.trim()}${b.getAttribute('aria-pressed') === 'true' ? '*' : ''}`);

describe('SpotlightSearch (DOM)', () => {
	it('renders nothing while closed', () => {
		show();
		expect(field()).toBeNull();
	});

	it('open: a field and every category up front — no quick actions, prefixes, guide or keycaps', () => {
		show({ open: true });
		const text = document.body.textContent ?? '';
		expect(field()?.placeholder).toBe('Search your financial world…');
		expect(chips()).toEqual(['All*', 'Documents', 'Insights']);
		expect(text).not.toContain('Quick actions');
		expect(text).not.toContain('How search works');
		expect(document.querySelector('kbd')).toBeNull();
	});

	it('the window event opens it on a category: scoped field, that chip pressed, All the way back', () => {
		show();
		openSearchPalette('document');
		flushSync();
		expect(field()?.placeholder).toBe('Search documents…');
		expect(chips()).toEqual(['All', 'Documents*', 'Insights']);
		// Tapping another category moves the scope; tapping All lifts it.
		document
			.querySelectorAll<HTMLButtonElement>(
				'[aria-label="Filter search results by category"] button'
			)[2]
			.click();
		flushSync();
		expect(field()?.placeholder).toBe('Search insights…');
		document
			.querySelector<HTMLButtonElement>('[aria-label="Filter search results by category"] button')!
			.click();
		flushSync();
		expect(field()?.placeholder).toBe('Search your financial world…');
		expect(chips()).toEqual(['All*', 'Documents', 'Insights']);
	});

	it('tapping the pressed category again lifts it — the way back on a phone, where All hides', () => {
		show();
		openSearchPalette('insight');
		flushSync();
		document
			.querySelectorAll<HTMLButtonElement>(
				'[aria-label="Filter search results by category"] button'
			)[2]
			.click();
		flushSync();
		expect(chips()).toEqual(['All*', 'Documents', 'Insights']);
	});

	it("the phone's round close is its own control, so hiding the dialog's close does not take it", () => {
		show({ open: true });
		const cancel = document.querySelector<HTMLButtonElement>('[data-slot=spotlight-cancel]');
		expect(cancel?.getAttribute('aria-label')).toBe('Close search');
		expect(cancel?.className).toContain('sm:hidden');
		expect(document.querySelector('[data-slot=dialog-close]')).not.toBeNull();
		cancel!.click();
		flushSync();
		// bits-ui keeps the content mounted through its exit transition; the state is what flips.
		const content = document.querySelector('[data-slot=dialog-content]');
		expect(content === null || content.getAttribute('data-state') === 'closed').toBe(true);
	});

	it('lists the same fund on two accounts instead of staying on Searching', async () => {
		// One instrument held twice used to share a list key. The throw rolled the result
		// back and the panel never left "Searching…".
		show({
			open: true,
			scopes: [{ type: 'holding', label: 'Holdings' }],
			routes: { holding: (id: string) => `/accounts/${id}` },
			client: new SearchClient(
				(q: string) => `/search?q=${q}`,
				async () =>
					Response.json({
						query: 'van',
						truncated: false,
						groups: [
							{
								type: 'holding',
								label: 'Holdings',
								hits: [
									{
										type: 'holding',
										id: 'GB00B5B74684',
										target: 'acc-a',
										title: 'Vanguard in the bond',
										subtitle: null,
										meta: null
									},
									{
										type: 'holding',
										id: 'GB00B5B74684',
										target: 'acc-b',
										title: 'Vanguard in the ISA',
										subtitle: null,
										meta: null
									}
								]
							}
						]
					})
			)
		});
		const input = field()!;
		input.value = 'van';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		await new Promise((resolve) => setTimeout(resolve, 250));
		flushSync();
		const text = document.body.textContent ?? '';
		expect(text).not.toContain('Searching');
		expect(text).toContain('Vanguard in the bond');
		expect(text).toContain('Vanguard in the ISA');
	});

	it('Backspace on an empty field lifts the category before anything closes', () => {
		show();
		openSearchPalette('insight');
		flushSync();
		expect(field()?.placeholder).toBe('Search insights…');
		field()!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Backspace', bubbles: true }));
		flushSync();
		expect(field()?.placeholder).toBe('Search your financial world…');
	});
});
