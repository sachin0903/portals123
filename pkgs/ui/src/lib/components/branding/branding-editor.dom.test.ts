/**
 * @vitest-environment jsdom
 *
 * THE bug: the studio would not load at all, in any browser, for every firm.
 *
 * The Design view (the DEFAULT view) previewed the selected design set's pages with
 * `materialiseWorkingPages(active.pages)`. That helper deep-copies with `structuredClone`, and
 * `active.pages` reads straight off `$state` — so it was handed a Svelte state PROXY, which
 * `structuredClone` refuses with a DataCloneError. The component threw while rendering, and a
 * component that throws while rendering has no page.
 *
 * It survived a full green gate because every existing test of this component reads its SOURCE as
 * text (`branding-editor.test.ts`, self-documented as doing so), and because on the SERVER there
 * are no proxies at all: `$state` is plain data under SSR, so `structuredClone` is perfectly happy
 * and even an SSR render would have passed. The defect exists only once the component is MOUNTED,
 * which is precisely the gap `*.dom.test.ts` exists for in this package.
 *
 * So this file mounts the real editor. The assertion is deliberately unglamorous -- that it
 * renders at all, and that the preview it renders is the page-aware one -- because "it renders"
 * is the whole of what was broken. It fails against the previous implementation with
 * `DataCloneError: [object Object] could not be cloned` and passes against the current one.
 *
 * Timers are left entirely alone (no fake timers anywhere here): faking `setTimeout` in this
 * package kills vitest's worker heartbeat, which surfaces as a hang rather than a failure.
 */
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { Component } from 'svelte';
import type { Branding } from '@graphene/auth';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let BrandingEditor: Component<Record<string, unknown>>;
let defaultPages: typeof import('./portal-pages.js').defaultPages;

beforeAll(async () => {
	// jsdom ships no matchMedia, and Svelte's `prefersReducedMotion` calls it AT MODULE SCOPE
	// (animated-money.svelte, reached through the preview). Installed before the dynamic imports
	// below for that reason -- a stub added after them is already too late.
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
	// Likewise absent from jsdom. PreviewFrame observes its own box to scale the miniature; a
	// no-op observer means it simply never gets a measurement, which is the state the real
	// component already handles (it falls back to the authored grid).
	class NoopResizeObserver {
		observe() {}
		unobserve() {}
		disconnect() {}
	}
	Object.defineProperty(window, 'ResizeObserver', { writable: true, value: NoopResizeObserver });
	(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = NoopResizeObserver;

	// jsdom implements no scrolling. The preview mounts a real ClientShell, which scrolls its main
	// element back to the top on navigation.
	Object.defineProperty(Element.prototype, 'scrollTo', { writable: true, value: () => {} });

	({ mount, unmount, flushSync } = await import('svelte'));
	BrandingEditor = (await import('./branding-editor.svelte'))
		.default as unknown as typeof BrandingEditor;
	({ defaultPages } = await import('./portal-pages.js'));
	// Generous: pulling this component's graph (previews, bits-ui) through a cold vite transform
	// beats the 10s default on Windows.
}, 180_000);

afterEach(() => {
	document.body.innerHTML = '';
});

/**
 * A firm as the loader delivers one: identity on the root, one named design set carrying pages.
 *
 * The PAGES matter and are not padding. `materialiseWorkingPages` only reaches its
 * `structuredClone` when there are pages to clone -- an empty set short-circuits to the platform
 * defaults -- so the first version of this fixture said `pages: null` and passed happily against
 * the broken component, testing nothing. A design set with no pages is also not the case worth
 * covering: a firm that has customised is the one that owns page state at all.
 */
function firm(): Branding {
	return {
		colors: { light: { primary: '#123456' }, dark: { primary: '#abcdef' } },
		mode: 'light',
		templates: [
			{
				id: 'trustees',
				name: 'Trustees',
				role_slugs: ['trustee'],
				colors: { light: { primary: '#0d5c3a' } },
				pages: defaultPages()
			}
		],
		default_template_id: 'trustees'
	} as unknown as Branding;
}

function mountEditor(client: Branding) {
	const target = document.createElement('div');
	document.body.append(target);
	const app = mount(BrandingEditor, {
		target,
		props: {
			client,
			platform: {} as Branding,
			name: 'Acme Wealth'
		}
	});
	flushSync();
	return { target, app };
}

describe('the studio, mounted', () => {
	it('renders the Design view a firm lands on', () => {
		// The regression: this threw DataCloneError before the fix, so nothing below ran.
		const { target, app } = mountEditor(firm());

		expect(target.textContent).toContain('Client portal design sets');
		// The hero colour and feel live on the Theme card, next to the brand colours.
		expect(target.textContent).toContain('The band behind the portfolio total');
		unmount(app);
	});

	it('renders for a firm with no design sets at all', () => {
		// The firm that customised its pages BEFORE design sets existed: `templates` absent, so the
		// editor falls back to the root branding -- which owns the pages, and therefore takes the
		// same clone path with the selected template null throughout.
		const { target, app } = mountEditor({
			mode: 'light',
			pages: defaultPages()
		} as unknown as Branding);

		expect(target.textContent).toContain('Client portal design sets');
		unmount(app);
	});
});
