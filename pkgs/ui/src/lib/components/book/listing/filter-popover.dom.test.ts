// The filtering surface MOUNTED, for what SSR cannot see: the funnel shows and hides the bar,
// a floating editor opened from a tag must not be dismissed by its own layers, and a burst of
// digits must be ONE call. `setTimeout` stays REAL — faking it kills vitest's worker heartbeat —
// and opening is POLLED, because bits-ui's first floating-ui measurement in a file is the slowest.
import { afterEach, describe, expect, it } from 'vitest';
import { mount, tick, unmount } from 'svelte';
import Harness from './filter-popover-harness.test.svelte';
import { DATE_PRESETS } from './date-presets.js';
import type { FilterDef } from './filter-panel.svelte';

// jsdom has no ResizeObserver. The amount tag's slider observes its track, and an effect that
// throws there is an unhandled error even when the assertions pass.
class NoopResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
globalThis.ResizeObserver ??= NoopResizeObserver as unknown as typeof ResizeObserver;
// bits-ui scrolls the chosen command item into view. jsdom has no implementation, and the
// missing method rejects after the assertion has already passed.
Element.prototype.scrollIntoView ??= () => {};

const dated: FilterDef[] = [
	{ key: 'status', label: 'Status', options: [{ value: 'active', label: 'Active' }] },
	{ key: 'opened', label: 'Opened', options: [], kind: 'daterange' }
];
const ranged: FilterDef[] = [{ key: 'value', label: 'Value', options: [], kind: 'numberrange' }];

type Listing = {
	read: () => Record<string, string[]>;
	writes: () => number;
	wipe: () => void;
};
const live: ReturnType<typeof mount>[] = [];

afterEach(() => {
	for (const component of live.splice(0)) unmount(component);
	document.body.innerHTML = '';
});

const find = (s: string) => document.body.querySelector<HTMLElement>(s);
const bar = () => find('[data-slot="filter-bar"]');
const funnel = () => find('[aria-label^="Show filters"], [aria-label^="Hide filters"]');

async function press(el: Element) {
	el.dispatchEvent(new Event('pointerdown', { bubbles: true }));
	el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
	await settle();
}

async function settle() {
	await tick();
	await new Promise((resolve) => setTimeout(resolve, 20));
	await tick();
}

async function until(condition: () => boolean, ms = 2000) {
	const deadline = Date.now() + ms;
	while (!condition() && Date.now() < deadline) await settle();
}

async function opened(available: FilterDef[], initial: Record<string, string[]> = {}) {
	const target = document.createElement('div');
	document.body.append(target);
	const component = mount(Harness, { target, props: { available, initial } });
	live.push(component);
	await settle();
	// A filtered arrival already has the bar open. Clicking then would hide it.
	if (funnel()?.getAttribute('aria-label')?.startsWith('Show')) await press(funnel()!);
	await until(() => !!bar());
	expect(bar()).toBeTruthy();
	return component as unknown as Listing;
}

const typeInto = async (box: HTMLInputElement, value: string) => {
	box.value = value;
	box.dispatchEvent(new Event('input', { bubbles: true }));
	await settle();
};

async function openTag(label: string) {
	const trigger = [...(bar()?.querySelectorAll('button') ?? [])].find(
		(button) =>
			(button.textContent ?? '').includes(label) &&
			!button.getAttribute('aria-label')?.startsWith('Clear')
	);
	if (!trigger) throw new Error(`no ${label} tag`);
	await press(trigger);
	return trigger;
}

describe('the filter bar, mounted', () => {
	it('lists every field, hides their options, and the funnel shows and hides the bar', async () => {
		const listing = await opened(dated, { status: ['active'] });
		const row = bar()!;
		expect(row.textContent).toContain('Status');
		expect(row.textContent).toContain('Active');
		expect(row.textContent).toContain('Opened');
		expect(row.className).toContain('bg-muted');
		expect(row.className).toContain('dark:bg-background');
		// The option list belongs to the tag's editor, not the bar.
		expect(row.querySelector('[data-slot="popover-content"]')).toBeNull();
		expect(find('[data-slot="dropdown-menu-content"]')).toBeNull();
		expect(find('[aria-label="Clear status filter"]')).toBeTruthy();
		expect(find('[aria-label="Clear opened filter"]')).toBeNull();

		await press(funnel()!);
		await until(() => !bar());
		expect(bar()).toBeNull();
		// Hiding the bar leaves the applied value where it was.
		expect(listing.read()).toEqual({ status: ['active'] });
		expect(listing.writes()).toBe(0);
		expect(funnel()?.getAttribute('aria-label')).toBe('Show filters (1 applied)');

		await press(funnel()!);
		await until(() => (bar()?.textContent ?? '').includes('Opened'));
		expect(bar()?.textContent).toContain('Status');
		expect(bar()?.textContent).toContain('Active');
	});

	it('clears a set value and leaves the field on the bar', async () => {
		const listing = await opened(dated, { status: ['active'] });
		await press(find('[aria-label="Clear status filter"]')!);
		await until(() => listing.read().status === undefined);
		expect(listing.read().status).toBeUndefined();
		expect(listing.writes()).toBe(1);
		expect(bar()?.textContent).toContain('Status');
		expect(bar()?.textContent).toContain('Opened');
		expect(bar()?.textContent).not.toContain('Active');
		expect(find('[aria-label="Clear status filter"]')).toBeNull();
		expect(funnel()?.getAttribute('aria-label')).toBe('Hide filters');
	});

	it('tells the listing ONCE for a whole number typed into a range', async () => {
		const listing = await opened(ranged);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		const box = find('[aria-label="Value from"]') as HTMLInputElement;
		for (const t of ['3', '30', '300', '3000']) await typeInto(box, t);
		expect(listing.writes()).toBe(0);
		expect(box.value).toBe('3000');
		await until(() => listing.writes() > 0, 3000);
		expect(listing.writes()).toBe(1);
		expect(listing.read()).toEqual({ value: ['3000', ''] });
	});

	it('flushes when the tag closes rather than dropping the last thing typed', async () => {
		const listing = await opened(ranged);
		const tag = await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		await typeInto(find('[aria-label="Value from"]') as HTMLInputElement, '99');
		expect(listing.writes()).toBe(0);
		await press(tag);
		await until(() => listing.writes() > 0);
		expect(listing.read()).toEqual({ value: ['99', ''] });
		expect(listing.writes()).toBe(1);
	});

	it('opens values in place: ticks and counts, a search box only from eight options', async () => {
		const few = Array.from({ length: 3 }, (_, i) => ({
			value: `s${i}`,
			label: `Status ${i}`,
			count: 10 + i
		}));
		await opened([{ key: 'status', label: 'Status', options: few }], { status: ['s0'] });
		await openTag('Status');
		await until(() => !!find('[data-slot="popover-content"] [data-state="checked"]'));
		const panel = find('[data-slot="popover-content"]')!;
		expect(panel.textContent).toContain('Status 2');
		expect(panel.textContent).toContain('12');
		expect(panel.querySelector('input[type="checkbox"]')).toBeNull();
		expect(panel.querySelector('[data-slot="command-input"]')).toBeNull();
		expect(panel.querySelector('[data-state="checked"]')?.textContent).toContain('Status 0');
		expect(panel.querySelector('[data-state="checked"] svg')).toBeTruthy();
		const tag = find('[data-slot="filter-tag"] button')!;
		expect(tag.className).toContain('cursor-pointer');
	});

	it('adds a search box once a filter has eight options, and still lists them in place', async () => {
		const many = Array.from({ length: 8 }, (_, i) => ({
			value: `t${i}`,
			label: `Type ${i}`
		}));
		await opened([{ key: 'type', label: 'Client type', options: many }]);
		await openTag('Client type');
		await until(() => !!find('[data-slot="popover-content"] [data-slot="command-input"]'));
		const panel = find('[data-slot="popover-content"]')!;
		expect(panel.textContent).toContain('Type 7');
		expect(panel.querySelector('[data-slot="command-input"]')).toBeTruthy();
	});

	it('keeps a typed amount when the column has a track to snap it to', async () => {
		// The slider's step is a hundredth of the span. "25" is not on that step, and it sits
		// within half a step of the low end, which the track reads as "no bound" — so the digit
		// used to vanish as it was typed.
		const listing = await opened([
			{ key: 'value', label: 'Value', options: [], kind: 'numberrange', min: '0', max: '100000' }
		]);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		const box = find('[aria-label="Value from"]') as HTMLInputElement;
		await typeInto(box, '25');
		expect(box.value).toBe('25');
		await until(() => listing.writes() > 0, 3000);
		expect(listing.read()).toEqual({ value: ['25', ''] });
		expect(box.value).toBe('25');
	});

	it('keeps 100, which the track snaps to its end, and 1000, which is a step', async () => {
		// Span 100,000 → step 1,000. 100 is within half a step of the low end, so a slider write
		// of that snap is "no bound" and used to clear the field. 1,000 is a stop, so it survived.
		// The pointerdown is released off the track: that used to leave the gesture flag set, and
		// the next snap then wiped whatever had been typed.
		const listing = await opened([
			{ key: 'value', label: 'Value', options: [], kind: 'numberrange', min: '0', max: '100000' }
		]);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		find('[data-slot="slider"]')!.dispatchEvent(
			new PointerEvent('pointerdown', {
				bubbles: true,
				cancelable: true,
				button: 0,
				clientX: 8,
				clientY: 8
			})
		);
		const box = find('[aria-label="Value from"]') as HTMLInputElement;
		await typeInto(box, '100');
		await until(() => listing.read().value?.[0] === '100', 3000);
		expect(listing.read()).toEqual({ value: ['100', ''] });
		expect(box.value).toBe('100');
		await typeInto(box, '1000');
		await until(() => listing.read().value?.[0] === '1000', 3000);
		expect(listing.read()).toEqual({ value: ['1000', ''] });
		expect(box.value).toBe('1000');
	});

	it('keeps a typed amount in a money field', async () => {
		const listing = await opened([
			{
				key: 'value',
				label: 'Value',
				options: [],
				kind: 'numberrange',
				currency: 'GBP',
				min: '0',
				max: '100000'
			}
		]);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		const box = find('[aria-label="Value from"]') as HTMLInputElement;
		await typeInto(box, '25');
		// The popover moves focus, so the field settles pence. The amount itself must stay.
		expect(box.value).toBe('25.00');
		await until(() => listing.writes() > 0, 3000);
		expect(listing.read()).toEqual({ value: ['25', ''] });
		await typeInto(box, '100');
		await until(() => listing.read().value?.[0] === '100', 3000);
		expect(listing.read()).toEqual({ value: ['100', ''] });
		expect(box.value).toContain('100');
	});

	it('caps a from amount at the to already entered', async () => {
		const listing = await opened([
			{
				key: 'value',
				label: 'Value',
				options: [],
				kind: 'numberrange',
				currency: 'GBP',
				min: '0',
				max: '100000'
			}
		]);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value to"]'));
		const from = find('[aria-label="Value from"]') as HTMLInputElement;
		const to = find('[aria-label="Value to"]') as HTMLInputElement;
		await typeInto(to, '1000');
		await until(() => listing.read().value?.[1] === '1000', 3000);
		await typeInto(from, '2500');
		await until(() => listing.read().value?.[0] === '1000', 3000);
		expect(listing.read()).toEqual({ value: ['1000', '1000'] });
		expect(from.value).toContain('1,000');
		await typeInto(from, '400');
		await until(() => listing.read().value?.[0] === '400', 3000);
		expect(listing.read()).toEqual({ value: ['400', '1000'] });
		expect(from.value).toContain('400');
		// To is capped when the field is left, not on the first digit of a new amount.
		await typeInto(to, '100');
		await until(() => listing.read().value?.[1] === '100', 3000);
		expect(listing.read()).toEqual({ value: ['400', '100'] });
		to.dispatchEvent(new FocusEvent('blur'));
		await until(() => listing.read().value?.[0] === '100', 3000);
		expect(listing.read()).toEqual({ value: ['100', '100'] });
	});

	it('does not put a half-typed amount back when the bar is hidden', async () => {
		const listing = await opened(ranged);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		await typeInto(find('[aria-label="Value from"]') as HTMLInputElement, '25');
		expect(listing.writes()).toBe(0);
		await press(funnel()!);
		await new Promise((resolve) => setTimeout(resolve, 400));
		await tick();
		expect(listing.writes()).toBe(0);
		expect(listing.read().value).toBeUndefined();
		expect(bar()).toBeNull();
	});

	it('does not put a half-typed amount back when its value is cleared', async () => {
		const listing = await opened(ranged, { value: ['10', ''] });
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		await typeInto(find('[aria-label="Value from"]') as HTMLInputElement, '40');
		expect(listing.writes()).toBe(0);
		await press(find('[aria-label="Clear value filter"]')!);
		await new Promise((resolve) => setTimeout(resolve, 400));
		await tick();
		// The clear itself is the one write. The half-typed 40 must not follow it.
		expect(listing.writes()).toBe(1);
		expect(listing.read().value).toBeUndefined();
		expect(bar()?.textContent).toContain('Value');
		expect(find('[aria-label="Value from"]')).toBeNull();
	});

	it('drops a half-typed amount when the listing clears the applied filters itself', async () => {
		// The bar is up because a filter arrived applied — the reader never opened it — so an
		// external clear shuts it as well as emptying the values.
		const listing = await opened(
			[
				{ key: 'status', label: 'Status', options: [{ value: 'active', label: 'Active' }] },
				...ranged
			],
			{ status: ['active'] }
		);
		await openTag('Value');
		await until(() => !!find('[aria-label="Value from"]'));
		await typeInto(find('[aria-label="Value from"]') as HTMLInputElement, '25');
		await until(() => listing.read().value?.[0] === '25', 3000);
		const writes = listing.writes();
		listing.wipe();
		await settle();
		await new Promise((resolve) => setTimeout(resolve, 400));
		await tick();
		expect(listing.read().value).toBeUndefined();
		expect(listing.read().status).toBeUndefined();
		expect(listing.writes()).toBe(writes);
		expect(document.querySelector('[data-slot="filter-bar"]')).toBeNull();
	});

	it('keeps the calendar up when its preset list opens, and the list is the short one', async () => {
		await opened(dated, { opened: ['2026-01-01', '2026-06-30'] });
		const dateTag = find('[aria-label^="Opened"]');
		expect(dateTag).toBeTruthy();
		await press(dateTag!);
		await until(() => !!find('[aria-label="Date presets"]'));
		expect(find('[aria-label="Date presets"]')).toBeTruthy();
		const presetTrigger = find('[aria-label="Date presets"]')!;
		// ArrowDown is how the select opens from the keyboard. A synthetic click does not: bits-ui
		// opens it on pointerdown, and only when the event's button is the left one.
		presetTrigger.focus();
		presetTrigger.dispatchEvent(
			new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true })
		);
		await settle();
		await until(() => document.body.textContent?.includes('Last 24 hr') ?? false);
		for (const preset of DATE_PRESETS) expect(document.body.textContent).toContain(preset.label);
		expect(document.querySelectorAll('[data-slot="select-separator"]')).toHaveLength(2);
		expect(document.querySelector('[data-slot="select-item"]')?.className).toContain('text-xs');
		expect(document.body.textContent).not.toContain('Last 90');
		expect(document.body.textContent).not.toContain('All Time');
		// The preset menu is a layer inside the calendar. Opening it must not dismiss the grid.
		expect(find('[aria-label="Date presets"]')).toBeTruthy();
		expect(document.body.textContent).toContain('Clear dates');
	});

	it('hides an autocomplete list until something is typed, and keeps a chosen value', async () => {
		const advisers = [
			{ value: 'dana', label: 'Dana Cole' },
			{ value: 'sam', label: 'Sam Shaw' }
		];
		await opened([
			{
				key: 'adviser',
				label: 'Adviser',
				options: advisers,
				autocomplete: true,
				searchPlaceholder: 'Search advisers…'
			}
		]);
		await openTag('Adviser');
		await until(() => !!find('[data-slot="popover-content"] [data-slot="command-input"]'));
		const panel = find('[data-slot="popover-content"]')!;
		expect(panel.textContent).toContain('Type to search');
		expect(panel.textContent).not.toContain('Dana Cole');
		expect(panel.textContent).not.toContain('Sam Shaw');

		const box = panel.querySelector('[data-slot="command-input"]') as HTMLInputElement;
		await typeInto(box, 'Dan');
		await until(() => panel.textContent?.includes('Dana Cole') ?? false);
		expect(panel.textContent).toContain('Dana Cole');
		expect(panel.textContent).not.toContain('Sam Shaw');

		await press(
			[...panel.querySelectorAll('[data-slot="command-item"]')].find((item) =>
				item.textContent?.includes('Dana Cole')
			)!
		);
		await until(() => (bar()?.textContent ?? '').includes('Dana Cole'));
		expect(bar()?.textContent).toContain('Dana Cole');
	});
});
