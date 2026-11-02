/**
 * SSR pins for the dynamic back target.
 *
 * The bug these pin: a back link was whatever the call site declared, and a call site can only
 * declare the route's PARENT. `task-detail.svelte` says `back={{ href: links.tasks(), label:
 * 'Tasks' }}`, so a task opened from the dashboard's action queue offered "Tasks" — a page the user
 * had never visited. An app that knows the navigation history publishes it through the `page-back`
 * context and it wins.
 *
 * The third case in each block is the one that protects the admin and client portals: they publish
 * no context, and their output must be byte-identical to before.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import PageHeader from './page-header.svelte';
import { PAGE_BACK_KEY, type PageBackSource } from './page-back.js';
import type { BackLink } from './types.js';

const seeded = (current: BackLink | null) =>
	new Map<symbol, PageBackSource>([[PAGE_BACK_KEY, { current }]]);

const declared: BackLink = { href: '/tasks', label: 'Tasks' };
const actual: BackLink = { href: '/', label: 'Dashboard' };

describe('PageHeader back link', () => {
	it('prefers where you actually came from over the declared parent', () => {
		const { body } = render(PageHeader, {
			props: { title: 'Chase the LOA', back: declared },
			context: seeded(actual)
		});
		expect(body).toContain('href="/"');
		expect(body).toContain('Dashboard');
		expect(body).not.toContain('href="/tasks"');
		expect(body).not.toContain('Tasks');
	});

	it('falls back to the declared parent when there is no history', () => {
		// A hard reload, a pasted link, a new tab. The declared parent is the right answer here and
		// only here — which is why it survives as the fallback rather than being deleted.
		const { body } = render(PageHeader, {
			props: { title: 'Chase the LOA', back: declared },
			context: seeded(null)
		});
		expect(body).toContain('href="/tasks"');
		expect(body).toContain('Tasks');
	});

	it('renders exactly as before in an app that publishes no context', () => {
		const { body } = render(PageHeader, { props: { title: 'Chase the LOA', back: declared } });
		expect(body).toContain('href="/tasks"');
		expect(body).toContain('Tasks');
	});

	it('never CREATES a back link on a page that declares none', () => {
		// Without this guard the override would grow a back link on the ~200 screens that have never
		// had one, and show a second copy of the topbar's control on the screens that do. The context
		// overrides an existing control; it does not invent one.
		const { body } = render(PageHeader, {
			props: { title: 'Tasks' },
			context: seeded(actual)
		});
		expect(body).not.toContain('href="/"');
		expect(body).not.toContain('Dashboard');
	});
});
