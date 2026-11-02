/**
 * What Enter does in the box — the one composer rule that differs by chair. Mounted in jsdom
 * because a keystroke only exists after mount, and because the rule reads `matchMedia`.
 */
import { beforeAll, describe, expect, it } from 'vitest';
import type { Component } from 'svelte';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let MessageComposer: Component<Record<string, unknown>>;

/** What `(pointer: fine)` answers — a keyboard in front of a screen, or a thumb on glass. */
let finePointer = true;

beforeAll(async () => {
	// jsdom ships no matchMedia; Svelte's `prefersReducedMotion` calls it at module scope through
	// the `@graphene/ui` barrel, so the stub goes in before the dynamic imports below.
	Object.defineProperty(window, 'matchMedia', {
		writable: true,
		value: (query: string) => ({
			matches: query === '(pointer: fine)' ? finePointer : false,
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
	MessageComposer = (await import('./message-composer.svelte')).default as unknown as Component<
		Record<string, unknown>
	>;
}, 180_000);

const open = (props: Record<string, unknown>) => {
	const target = document.body.appendChild(document.createElement('div'));
	let sent = 0;
	const component = mount(MessageComposer, {
		target,
		props: { draft: 'hello', submit: () => (sent += 1), ...props }
	});
	flushSync();
	const box = target.querySelector<HTMLTextAreaElement>('textarea')!;
	const press = (init: KeyboardEventInit) => {
		box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, ...init }));
		flushSync();
	};
	return { target, component, press, sent: () => sent };
};

describe('MessageComposer — Enter', () => {
	it('sends on Ctrl/Cmd+Enter in every register, and on bare Enter in none by default', () => {
		const c = open({});
		c.press({});
		expect(c.sent()).toBe(0);
		c.press({ ctrlKey: true });
		c.press({ metaKey: true });
		expect(c.sent()).toBe(2);
		unmount(c.component);
	});

	it('with enterSends, bare Enter sends on a fine pointer and Shift+Enter still breaks a line', () => {
		finePointer = true;
		const c = open({ enterSends: true });
		c.press({ shiftKey: true });
		expect(c.sent()).toBe(0);
		c.press({});
		expect(c.sent()).toBe(1);
		unmount(c.component);
	});

	it('with enterSends, bare Enter stays a newline on a touch device', () => {
		// The on-screen keyboard's return key is for lines; the Send button is right there.
		finePointer = false;
		const c = open({ enterSends: true });
		c.press({});
		expect(c.sent()).toBe(0);
		c.press({ ctrlKey: true });
		expect(c.sent()).toBe(1);
		unmount(c.component);
		finePointer = true;
	});
});

describe('MessageComposer — compact', () => {
	it('puts the paperclip, the box and Send on one row, in a pill', () => {
		const c = open({ compact: true });
		const form = c.target.querySelector('form')!;
		// A --radius multiple, not Tailwind's fixed 3xl step (token-hygiene.test.ts).
		expect(form.className).toContain('rounded-[calc(var(--radius)*2.4)]');
		const row = c.target.querySelector('textarea')!.parentElement!;
		expect(row.querySelector('[aria-label="Attach files"]')).not.toBeNull();
		expect(row.querySelector('[aria-label="Send message"]')).not.toBeNull();
		unmount(c.component);
	});

	it('keeps the staff shape by default — the box above, the actions on a row beneath', () => {
		const c = open({});
		expect(c.target.querySelector('form')!.className).toContain('rounded-xl');
		const row = c.target.querySelector('[aria-label="Send message"]')!.parentElement!;
		expect(row.querySelector('textarea')).toBeNull();
		unmount(c.component);
	});
});
