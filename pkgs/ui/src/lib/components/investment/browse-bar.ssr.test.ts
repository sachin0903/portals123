import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import BrowseBar from './browse-bar.svelte';

/**
 * SSR pins for the portal's one browse bar — the Insights library and the Docs page share it,
 * so these pin the contract both lean on: tabs with a pressed state, the optional pull-down, and
 * search as a button that hands off to the portal's palette (never a second search box).
 */

const tabs = [
	{ value: 'all', label: 'All' },
	{ value: 'Statements', label: 'Statements' },
	{ value: 'Tax', label: 'Tax' }
];

const base = {
	tabs,
	tab: 'Statements',
	ontab: () => {},
	tabsLabel: 'Filter documents by category'
};

describe('BrowseBar (SSR)', () => {
	it('renders the tabs as a labelled group with the current one pressed', () => {
		const body = render(BrowseBar, { props: base }).body;
		expect(body).toContain('aria-label="Filter documents by category"');
		expect(body).toMatch(/aria-pressed="true"[^>]*>\s*Statements\s*</);
		expect(body).toMatch(/aria-pressed="false"[^>]*>\s*All\s*</);
		// The brand indicator sits under the pressed tab only.
		expect(body.split('after:bg-[var(--brand)]').length - 1).toBe(1);
	});

	it('a single tab renders no tab group — nothing to switch between', () => {
		const body = render(BrowseBar, { props: { ...base, tabs: tabs.slice(0, 1) } }).body;
		expect(body).not.toContain('aria-label="Filter documents by category"');
	});

	it('no search prop ⇒ no search button; a search prop ⇒ a button, never a box of its own', () => {
		expect(render(BrowseBar, { props: base }).body).not.toContain('aria-label="Search documents"');
		const body = render(BrowseBar, {
			props: { ...base, search: { label: 'Search documents', scope: 'document' } }
		}).body;
		expect(body).toContain('aria-label="Search documents"');
		expect(body).not.toContain('type="search"');
		expect(body).not.toContain('<input');
	});

	it('the pull-down names its heading and current value, and reads quiet at the default', () => {
		const menu = {
			heading: 'Show',
			options: [
				{ value: 'all', label: 'All types' },
				{ value: 'guide', label: 'Guides' }
			],
			value: 'all',
			onchange: () => {},
			quiet: true
		};
		const quiet = render(BrowseBar, { props: { ...base, menu } }).body;
		expect(quiet).toContain('aria-label="Show: All types"');
		expect(quiet).toContain('text-muted-foreground');
		const narrowed = render(BrowseBar, {
			props: { ...base, menu: { ...menu, value: 'guide', quiet: false } }
		}).body;
		expect(narrowed).toContain('aria-label="Show: Guides"');
	});

	it('interactive={false} renders every control disabled (the studio preview)', () => {
		const body = render(BrowseBar, {
			props: {
				...base,
				interactive: false,
				search: { label: 'Search documents', scope: 'document' }
			}
		}).body;
		// Three tabs + the search button, all inert.
		expect(body.split(' disabled').length - 1).toBeGreaterThanOrEqual(4);
	});
});
