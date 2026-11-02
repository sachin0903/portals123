/**
 * The profile heatmap is a client chart: SSR only paints the control, and the cells
 * appear once the chart can measure itself. This mounts that path.
 */
import { beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { ActivityDay } from './activity-heatmap.svelte';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let ActivityHeatmap: Component<{ days?: ActivityDay[] }>;

const days: ActivityDay[] = [
	{ date: '2026-09-27', actions: 2, page_views: 0 },
	{ date: '2026-09-28', actions: 0, page_views: 5 },
	{ date: '2026-09-29', actions: 1, page_views: 1 }
];

beforeAll(async () => {
	class ResizeObserverStub {
		observe() {}
		unobserve() {}
		disconnect() {}
	}
	globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
	window.matchMedia = (query: string) =>
		({
			matches: false,
			media: query,
			addEventListener() {},
			removeEventListener() {},
			addListener() {},
			removeListener() {},
			dispatchEvent: () => false,
			onchange: null
		}) as MediaQueryList;
	({ mount, unmount } = await import('svelte'));
	ActivityHeatmap = (await import('./activity-heatmap.svelte')).default;
	// The first chart import takes most of the 30s hook budget alone, and the pre-push hook runs the
	// API suite beside it — the same margin the other chart-mounting DOM tests here take.
}, 120_000);

describe('ActivityHeatmap', () => {
	it('shows the series control and a cell for each day', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(ActivityHeatmap, { target, props: { days } });
		expect(target.textContent).toContain('Actions');
		expect(target.textContent).toContain('Page views');
		expect(target.textContent).toContain('Both');
		await vi.waitFor(() => {
			const titled = [...target.querySelectorAll('[data-day]')].map((node) =>
				node.getAttribute('aria-label')
			);
			expect(titled.some((title) => title?.includes('27 Sept 2026') && title.endsWith('2'))).toBe(
				true
			);
		});

		const pageViews = [...target.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('Page views')
		);
		pageViews?.click();
		await new Promise((resolve) => setTimeout(resolve, 0));
		const views = [...target.querySelectorAll('[data-day]')].map((node) =>
			node.getAttribute('aria-label')
		);
		expect(views.some((title) => title?.includes('27 Sept 2026') && title.endsWith('0'))).toBe(
			true
		);
		expect(views.some((title) => title?.includes('28 Sept 2026') && title.endsWith('5'))).toBe(
			true
		);

		unmount(component);
		target.remove();
	});

	it("shows the day's actions and page views straight away on hover", async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(ActivityHeatmap, { target, props: { days } });
		let cell: Element | undefined;
		await vi.waitFor(() => {
			cell = [...target.querySelectorAll('[data-day]')].find((node) =>
				node.getAttribute('aria-label')?.includes('29 Sept 2026')
			);
			expect(cell).toBeDefined();
		});
		cell?.dispatchEvent(new PointerEvent('pointerenter'));
		await new Promise((resolve) => setTimeout(resolve, 0));
		const tip = target.querySelector('[role="tooltip"]');
		expect(tip?.textContent).toContain('Tue, 29 Sept 2026');
		expect(tip?.textContent).toContain('1 action');
		expect(tip?.textContent).toContain('1 page view');

		cell?.dispatchEvent(new PointerEvent('pointerleave'));
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(target.querySelector('[role="tooltip"]')).toBeNull();

		unmount(component);
		target.remove();
	});

	it('says what each legend swatch means on hover', async () => {
		// "Less → More" named the direction and nothing else; the swatches now carry the band.
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(ActivityHeatmap, { target, props: { days } });
		await vi.waitFor(() => expect(target.textContent).toContain('Less'));
		const swatches = [...target.querySelectorAll('[role="img"][title]')].map((node) =>
			node.getAttribute('title')
		);
		// Busiest day has 5 events in "Both": five shades, from none to the top band.
		expect(swatches).toEqual([
			'No activity',
			'1 event in a day',
			'2 events in a day',
			'3 events in a day',
			'4–5 events in a day'
		]);

		const actions = [...target.querySelectorAll('button')].find((button) =>
			button.textContent?.includes('Actions')
		);
		actions?.click();
		await new Promise((resolve) => setTimeout(resolve, 0));
		const after = [...target.querySelectorAll('[role="img"][title]')].map((node) =>
			node.getAttribute('title')
		);
		expect(after.at(-1)).toBe('2 actions in a day');

		unmount(component);
		target.remove();
	});
});
