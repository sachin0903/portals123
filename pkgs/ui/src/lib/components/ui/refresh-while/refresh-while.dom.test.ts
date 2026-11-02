/**
 * @vitest-environment jsdom
 *
 * Each waiting screen asks for its load again only while that wait is still in flight.
 * This mounts the shared timer the pages use and asserts the callback — the load — fires
 * on that screen's interval, and does not fire once the wait has settled.
 */
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { Component } from 'svelte';
import { runInFlight } from '../../actions/run-states.js';
import { documentInFlight } from '../../book/documents/documents.js';
import { checksInFlight } from '../../book/journeys/steps.js';
import { REFRESH_WHILE } from '../../notifications/poll-controller.js';

type Props = { active: boolean; interval: number; onpoll: () => void };

let mount: typeof import('svelte').mount;
let unmount: typeof import('svelte').unmount;
let flushSync: typeof import('svelte').flushSync;
let RefreshWhile: Component<Props>;

beforeAll(async () => {
	vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
	({ mount, unmount, flushSync } = await import('svelte'));
	RefreshWhile = (await import('./refresh-while.svelte')).default as Component<Props>;
}, 120_000);

afterEach(() => {
	document.body.replaceChildren();
});

function watch(active: boolean, interval: number) {
	const onpoll = vi.fn();
	const target = document.createElement('div');
	document.body.appendChild(target);
	const app = mount(RefreshWhile, { target, props: { active, interval, onpoll } });
	flushSync();
	return {
		onpoll,
		destroy() {
			unmount(app);
		}
	};
}

const PAGES: { name: string; active: boolean; interval: number }[] = [
	{
		name: 'journey while an identity check is open',
		active: checksInFlight([{ steps: [{ step_type: 'verify_identity', status: 'waiting' }] }]),
		interval: REFRESH_WHILE.pendingCheck
	},
	{
		name: 'journey once the check has settled',
		active: checksInFlight([{ steps: [{ step_type: 'verify_identity', status: 'fulfilled' }] }]),
		interval: REFRESH_WHILE.pendingCheck
	},
	{
		name: 'document page and library while a render is queued',
		active: documentInFlight('queued'),
		interval: REFRESH_WHILE.documentRender
	},
	{
		name: 'document page once the PDF exists',
		active: documentInFlight('generated'),
		interval: REFRESH_WHILE.documentRender
	},
	{
		name: 'document library while a row is still generating',
		active: documentInFlight('generating'),
		interval: REFRESH_WHILE.inFlightList
	},
	{
		name: 'action runs while one is queued',
		active: runInFlight('queued'),
		interval: REFRESH_WHILE.actionRun
	},
	{
		name: 'action runs waiting on an approval',
		active: runInFlight('awaiting_approval'),
		interval: REFRESH_WHILE.actionRun
	}
];

describe('each waiting screen reloads only while the wait is open', () => {
	it.each(PAGES)('$name', ({ active, interval }) => {
		const { onpoll, destroy } = watch(active, interval);
		expect(onpoll).not.toHaveBeenCalled();
		vi.advanceTimersByTime(interval);
		expect(onpoll).toHaveBeenCalledTimes(active ? 1 : 0);
		if (active) {
			vi.advanceTimersByTime(interval);
			expect(onpoll).toHaveBeenCalledTimes(2);
		}
		destroy();
		vi.advanceTimersByTime(interval * 3);
		expect(onpoll).toHaveBeenCalledTimes(active ? 2 : 0);
	});
});
