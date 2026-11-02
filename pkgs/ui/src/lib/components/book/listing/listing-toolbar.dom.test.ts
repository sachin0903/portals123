import { afterEach, describe, expect, it } from 'vitest';
import { createRawSnippet, mount, tick, unmount } from 'svelte';
import ListingToolbar from './listing-toolbar.svelte';

const live: ReturnType<typeof mount>[] = [];
afterEach(() => {
	while (live.length) unmount(live.pop()!);
	document.body.innerHTML = '';
});

const filterable = {
	filterDefs: [{ key: 'status', label: 'Status', options: [{ value: 'active', label: 'Active' }] }],
	onfilters: () => {}
};

async function toolbar(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.append(target);
	live.push(mount(ListingToolbar, { target, props }));
	await tick();
	return target;
}

describe('Clear', () => {
	// ONE gesture, so ONE call. It fired `onsearch('')` then `onclear()` — two navigations, and the
	// second won: a caller that rebuilds its whole query from its props (the notifications feed)
	// read the term off a prop the first navigation had not landed in, so the box emptied while the
	// list stayed narrowed by a search the reader could no longer see.
	const press = async (props: Record<string, unknown>) => {
		const target = await toolbar({ query: 'ashworth', dirty: true, ...props });
		const button =
			[...target.querySelectorAll('button')].find((b) =>
				(b.textContent ?? '').trim().startsWith('Clear')
			) ?? target.querySelector<HTMLButtonElement>('[aria-label="Clear search"]');
		if (!button) throw new Error('no Clear button rendered');
		button.click();
		await tick();
		return target;
	};

	it('calls onclear ALONE, falls back to onsearch without one, and empties the box either way', async () => {
		const calls: string[] = [];
		const target = await press({
			onclear: () => calls.push('onclear'),
			onsearch: () => calls.push('onsearch')
		});
		expect(calls).toEqual(['onclear']);
		// The input owns its own text once mounted, so it is reset here, not by the navigation.
		expect((target.querySelector('input[type="search"]') as HTMLInputElement).value).toBe('');

		const bare: string[] = [];
		await press({ onsearch: (term: string) => bare.push(`onsearch:${term}`) });
		expect(bare).toEqual(['onsearch:']);
	});
});

describe('the funnel', () => {
	it('counts FILTERS not values in its accessible name, names them in the bar, builds no panel', async () => {
		// A button announced "Show filters" while filters are applied tells a screen-reader user the
		// opposite of what the badge shows the sighted one. A filtered arrival has the bar open, so
		// the control offers to hide it.
		const defs = [
			{ key: 'status', label: 'Status', options: [{ value: 'active', label: 'Active' }] },
			{ key: 'type', label: 'Type', options: [{ value: 'sipp', label: 'SIPP' }] }
		];
		const on = await toolbar({
			filterDefs: defs,
			onfilters: () => {},
			filterValue: { status: ['active'] }
		});
		expect(on.querySelector('[aria-label="Hide filters (1 applied)"]')).toBeTruthy();
		expect(on.querySelector('[aria-expanded="true"]')).toBeTruthy();
		const bar = on.querySelector('[data-slot="filter-bar"]');
		expect(bar?.textContent).toContain('Active');
		// Every filterable field is on the bar. An unset one does not carry a clear control.
		expect(bar?.textContent).toContain('Type');
		expect(bar?.textContent).not.toContain('SIPP');
		expect(bar?.className).toContain('bg-muted');
		expect(bar?.className).toContain('dark:bg-background');
		expect(on.querySelector('[aria-label="Clear status filter"]')).toBeTruthy();
		expect(on.querySelector('[aria-label="Clear type filter"]')).toBeNull();
		expect(document.body.textContent).not.toContain('Save filters');
		expect(document.body.textContent).not.toContain('Filter by');

		on.querySelector<HTMLButtonElement>('[aria-label="Hide filters (1 applied)"]')!.click();
		await tick();
		expect(on.querySelector('[data-slot="filter-bar"]')).toBeNull();
		expect(on.querySelector('[aria-label="Show filters (1 applied)"]')).toBeTruthy();
		expect(on.querySelector('[aria-expanded="false"]')).toBeTruthy();

		// A listing building its value from the URL hands over `{ status: [] }` unfiltered.
		const off = await toolbar({ ...filterable, filterValue: { status: [] } });
		expect(off.querySelector('[aria-label="Show filters"]')).toBeTruthy();
		expect(off.querySelector('[aria-expanded="false"]')).toBeTruthy();
		expect(off.querySelector('[data-slot="filter-bar"]')).toBeNull();
		off.querySelector<HTMLButtonElement>('[aria-label="Show filters"]')!.click();
		await tick();
		expect(off.querySelector('[data-slot="filter-bar"]')?.textContent).toContain('Status');
		expect(off.querySelector('[aria-label^="Clear"]')).toBeNull();
	});

	it('puts the ghost funnel beside search and keeps the named action as the right-hand anchor', async () => {
		const actions = createRawSnippet(() => ({
			render: () => '<button data-slot="button" data-testid="primary">New client</button>'
		}));
		const target = await toolbar({ ...filterable, actions });
		const search = target.querySelector('input[type="search"]')!;
		const primary = target.querySelector<HTMLElement>('[data-testid="primary"]')!;
		const funnel = target.querySelector<HTMLElement>('[aria-label="Show filters"]')!;
		const group = primary.parentElement!;

		// Ghost: no field border. The outline button this replaced drew one.
		expect(funnel.className).not.toContain('border-border');
		expect(funnel.className).toContain('cursor-pointer');
		expect(search.compareDocumentPosition(funnel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(funnel.compareDocumentPosition(primary) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		expect(group.contains(funnel)).toBe(false);
		expect(group.className).toContain('ml-auto');
		expect(group.className).toContain('min-h-9');
		expect(group.className).toContain('[&_[data-slot=button]]:h-9');
	});

	it('is muted at rest, and the current colour once a filter is applied', async () => {
		const rest = await toolbar(filterable);
		const idle = rest.querySelector<HTMLElement>('[aria-label="Show filters"]')!;
		expect(idle.className).toContain('text-muted-foreground');
		expect(idle.className).toContain('hover:text-foreground');
		expect(idle.className).not.toContain('text-primary');

		const applied = await toolbar({ ...filterable, filterValue: { status: ['active'] } });
		const open = applied.querySelector<HTMLElement>('[aria-label="Hide filters (1 applied)"]')!;
		open.click();
		await tick();
		const live = applied.querySelector<HTMLElement>('[aria-label="Show filters (1 applied)"]')!;
		expect(live.className).toContain('text-foreground');
		expect(live.className).not.toContain('text-muted-foreground');
		expect(live.className).not.toContain('text-primary');
	});
});

describe('Clear leaves with the filter bar', () => {
	it('keeps Clear inside the bar, and clears a bare search from the box', async () => {
		const applied = await toolbar({
			...filterable,
			dirty: true,
			filterValue: { status: ['active'] }
		});
		const bar = applied.querySelector('[data-slot="filter-bar"]')!;
		expect(bar.textContent).toContain('Clear');
		expect(applied.querySelector('[aria-label="Clear search"]')).toBeNull();

		const searched = await toolbar({ ...filterable, query: 'ashworth', dirty: true });
		expect(searched.querySelector('[data-slot="filter-bar"]')).toBeNull();
		expect(
			[...searched.querySelectorAll('button')].some((button) =>
				(button.textContent ?? '').trim().startsWith('Clear')
			)
		).toBe(false);
		expect(searched.querySelector('[aria-label="Clear search"]')).toBeTruthy();
	});

	it('draws no Clear once the bar has closed, even while the listing is still dirty', async () => {
		const closed = await toolbar({ ...filterable, dirty: true });
		expect(closed.querySelector('[data-slot="filter-bar"]')).toBeNull();
		expect(
			[...closed.querySelectorAll('button')].some((button) =>
				(button.textContent ?? '').trim().startsWith('Clear')
			)
		).toBe(false);
	});
});
