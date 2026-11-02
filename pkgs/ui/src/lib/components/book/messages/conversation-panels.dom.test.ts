/**
 * The search control's run-time behaviour — it is a BUTTON that becomes a box, and SSR cannot
 * press it. Mounted in jsdom because open/closed only exists after a click.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import type { Component } from 'svelte';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let createRawSnippet: typeof import('svelte').createRawSnippet;
let ConversationPanels: Component<Record<string, unknown>>;

beforeAll(async () => {
	// jsdom ships no matchMedia, and Svelte's `prefersReducedMotion` calls it AT MODULE SCOPE —
	// animated-money.svelte, reached through this component's `@graphene/ui` import. Installed
	// before the dynamic imports below for that reason: a stub added after them is already too late.
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
	({ mount, unmount, flushSync, createRawSnippet } = await import('svelte'));
	ConversationPanels = (await import('./conversation-panels.svelte'))
		.default as unknown as Component<Record<string, unknown>>;
	// Compiling this component for the client pulls the whole `@graphene/ui` barrel through vite,
	// which is well past the 10s default on a cold cache — the same margin branding-editor takes.
}, 180_000);

const open = () => {
	const target = document.body.appendChild(document.createElement('div'));
	const component = mount(ConversationPanels, {
		target,
		props: {
			messages: [],
			download: () => '#',
			children: createRawSnippet(() => ({ render: () => '<p>thread</p>' }))
		}
	});
	const button = target.querySelector<HTMLButtonElement>('[aria-label="Search this conversation"]');
	button?.click();
	flushSync();
	return { target, component };
};

const blur = (input: HTMLInputElement) => {
	input.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
	flushSync();
};

describe('the conversation search', () => {
	it('opens into a box when the button is pressed', () => {
		const { target, component } = open();
		expect(target.querySelector('input[type="search"]')).not.toBeNull();
		unmount(component);
	});

	it('folds back to a button when the reader leaves it empty', () => {
		// An empty box left open is the control still spending the row it was made a button to stop
		// spending.
		const { target, component } = open();
		blur(target.querySelector<HTMLInputElement>('input[type="search"]')!);
		expect(target.querySelector('input[type="search"]')).toBeNull();
		expect(target.querySelector('[aria-label="Search this conversation"]')).not.toBeNull();
		unmount(component);
	});

	it('stays open when a term is in it', () => {
		// Closing CLEARS the term, so folding on the way to a result in the thread it filtered would
		// throw that filter away under the reader.
		const { target, component } = open();
		const input = target.querySelector<HTMLInputElement>('input[type="search"]')!;
		input.value = 'transfer';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		blur(input);
		expect(target.querySelector('input[type="search"]')).not.toBeNull();
		unmount(component);
	});
});
