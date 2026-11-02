/**
 * @vitest-environment jsdom
 *
 * The hand-over: a streamed seed lands, the placeholder gives way to the live bell, and the bell
 * treats those rows as already announced — no card pops for them, and nothing is refetched to learn
 * what the loader just said.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import type { NotificationItem } from './describe.js';
// Runes are only available in a `.svelte.ts` module; this is the shared way a dom test hands a
// mounted component new props.
import { reactiveProps } from '../ui/streamed/streamed.harness.svelte.js';

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let StreamedNotificationBell: Component<Record<string, unknown>>;

beforeAll(async () => {
	globalThis.ResizeObserver ??= class {
		observe() {}
		unobserve() {}
		disconnect() {}
	} as unknown as typeof ResizeObserver;
	({ mount, unmount, flushSync } = await import('svelte'));
	StreamedNotificationBell = (await import('./streamed-notification-bell.svelte'))
		.default as typeof StreamedNotificationBell;
}, 120_000);

afterEach(() => {
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
});

function notification(uid: string): NotificationItem {
	return {
		uid,
		event_type: 'task.assigned',
		category: 'tasks',
		title: 'A task was assigned to you',
		body: null,
		link: `/tasks/${uid}`,
		priority: 'normal',
		subject_type: null,
		subject_id: null,
		read_at: null,
		created_at: new Date().toISOString()
	};
}

/** A stream that connects and nothing more, so the bell's own first-connect rule is in play. */
class FakeEventSource {
	static last: FakeEventSource | null = null;
	listeners = new Map<string, ((event?: unknown) => void)[]>();
	constructor(readonly url: string) {
		FakeEventSource.last = this;
	}
	addEventListener(type: string, fn: (event?: unknown) => void) {
		this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]);
	}
	close() {}
	emit(type: string, event: unknown = {}) {
		for (const fn of this.listeners.get(type) ?? []) fn(event);
	}
}

const settle = async () => {
	flushSync();
	await new Promise((r) => setTimeout(r, 0));
	flushSync();
};

const trigger = (root: ParentNode) =>
	root.querySelector<HTMLElement>('[aria-label^="Notifications"]');
const placeholder = (root: ParentNode) =>
	root.querySelector<HTMLElement>('[data-notification-bell-placeholder]');

describe('StreamedNotificationBell hand-over', () => {
	it('mounts the live bell with the seed once it lands, and asks the server for nothing', async () => {
		const fetch = vi.fn(async () => new Response(JSON.stringify({ items: [] }), { status: 200 }));
		vi.stubGlobal('fetch', fetch);
		vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);

		let resolveItems!: (items: NotificationItem[]) => void;
		let resolveCount!: (count: number) => void;
		const items = new Promise<NotificationItem[]>((r) => (resolveItems = r));
		const unreadCount = new Promise<number>((r) => (resolveCount = r));

		const target = document.createElement('div');
		document.body.append(target);
		const bell = mount(StreamedNotificationBell, {
			target,
			props: {
				items,
				unreadCount,
				toastArrivals: false,
				countEndpoint: '/notifications/unread-count',
				feedEndpoint: '/notifications/recent',
				realtimeUrl: '/notifications/events'
			}
		});
		flushSync();

		// Before the seed: the placeholder, no trigger, and no stream yet either — the live bell is
		// what opens it, and it has not mounted.
		expect(placeholder(target)).not.toBeNull();
		expect(trigger(target)).toBeNull();
		expect(FakeEventSource.last).toBeNull();

		resolveItems([notification('a'), notification('b')]);
		resolveCount(2);
		await settle();

		// After: the live bell, seeded with the count, and the stream opened by it.
		expect(placeholder(target)).toBeNull();
		expect(trigger(target)?.getAttribute('aria-label')).toBe('Notifications (2 unread)');
		expect(FakeEventSource.last).not.toBeNull();

		// The stream's first connect does not refetch what the seed just said.
		FakeEventSource.last!.emit('open');
		await settle();
		expect(fetch).not.toHaveBeenCalled();

		unmount(bell);
	});

	it('passes a later answer through as a prop update rather than remounting', async () => {
		vi.stubGlobal('fetch', vi.fn());
		vi.stubGlobal('EventSource', FakeEventSource as unknown as typeof EventSource);
		FakeEventSource.last = null;

		const target = document.createElement('div');
		document.body.append(target);
		const props = reactiveProps({
			items: Promise.resolve([notification('a')]) as
				NotificationItem[] | Promise<NotificationItem[]>,
			unreadCount: Promise.resolve(1) as number | Promise<number>,
			toastArrivals: false,
			realtimeUrl: '/notifications/events'
		});
		const bell = mount(StreamedNotificationBell, { target, props });
		await settle();
		expect(trigger(target)?.getAttribute('aria-label')).toBe('Notifications (1 unread)');
		const first = FakeEventSource.last;

		// The next navigation streams a fresh answer.
		props.items = Promise.resolve([notification('a'), notification('b'), notification('c')]);
		props.unreadCount = Promise.resolve(3);
		await settle();

		expect(trigger(target)?.getAttribute('aria-label')).toBe('Notifications (3 unread)');
		// Same bell, same stream: the wrapper updated the props rather than tearing the bell down.
		expect(FakeEventSource.last).toBe(first);

		unmount(bell);
	});
});
