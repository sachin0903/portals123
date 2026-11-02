import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPollController, pollWhileVisible } from './poll-controller.js';

const INTERVAL = 30_000;

describe('createPollController — visibility-aware polling', () => {
	beforeEach(() => vi.useFakeTimers());
	afterEach(() => vi.useRealTimers());

	it('polls every interval once started (no immediate poll)', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		expect(poll).toHaveBeenCalledTimes(0); // start() does NOT fire immediately
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(1);
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(2);
	});

	it('start() is idempotent — never stacks a second interval', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		c.start();
		c.start();
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(1); // one interval, not three
	});

	it('hidden() pauses — no polls fire while hidden', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(1);
		c.hidden();
		vi.advanceTimersByTime(INTERVAL * 5);
		expect(poll).toHaveBeenCalledTimes(1); // still 1 — paused
	});

	it('visible() RESUMES after hidden — refetches immediately and restarts the interval', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		c.hidden();
		vi.advanceTimersByTime(INTERVAL * 3);
		expect(poll).toHaveBeenCalledTimes(0); // paused before any tick

		c.visible();
		expect(poll).toHaveBeenCalledTimes(1); // immediate refetch on return
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(2); // interval resumed
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(3);
	});

	it('visible() while already running does not stack the interval', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		c.visible(); // fires one immediate poll, but must NOT add a second interval
		expect(poll).toHaveBeenCalledTimes(1); // the immediate refetch only
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(2); // one interval tick, not two
	});

	it('stop() tears down — no further polls', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		c.stop();
		vi.advanceTimersByTime(INTERVAL * 5);
		expect(poll).toHaveBeenCalledTimes(0);
	});

	it('survives a hidden→visible→hidden→visible cycle, always resuming exactly one interval', () => {
		const poll = vi.fn();
		const c = createPollController(INTERVAL, poll);
		c.start();
		c.hidden();
		c.visible(); // +1 immediate
		c.hidden();
		c.visible(); // +1 immediate
		expect(poll).toHaveBeenCalledTimes(2);
		vi.advanceTimersByTime(INTERVAL);
		expect(poll).toHaveBeenCalledTimes(3); // exactly one live interval, not several
	});
});

describe('pollWhileVisible', () => {
	let hidden = false;
	const listeners = new Map<string, Set<() => void>>();

	function add(type: string, fn: () => void) {
		const set = listeners.get(type) ?? new Set();
		set.add(fn);
		listeners.set(type, set);
	}
	function remove(type: string, fn: () => void) {
		listeners.get(type)?.delete(fn);
	}
	function emit(type: string) {
		for (const fn of listeners.get(type) ?? []) fn();
	}

	beforeEach(() => {
		vi.useFakeTimers();
		hidden = false;
		listeners.clear();
		vi.stubGlobal('document', {
			get hidden() {
				return hidden;
			},
			addEventListener: add,
			removeEventListener: remove
		});
		vi.stubGlobal('window', { addEventListener: add, removeEventListener: remove });
	});
	afterEach(() => {
		vi.unstubAllGlobals();
		vi.useRealTimers();
	});

	it('asks on the interval while the tab is visible, and not before it', () => {
		const poll = vi.fn();
		const stop = pollWhileVisible(1_000, poll);
		expect(poll).not.toHaveBeenCalled();
		vi.advanceTimersByTime(1_000);
		expect(poll).toHaveBeenCalledTimes(1);
		stop();
		vi.advanceTimersByTime(5_000);
		expect(poll).toHaveBeenCalledTimes(1);
	});

	it('pauses while the tab is hidden and asks at once when it returns', () => {
		const poll = vi.fn();
		const stop = pollWhileVisible(1_000, poll);
		hidden = true;
		emit('visibilitychange');
		vi.advanceTimersByTime(5_000);
		expect(poll).not.toHaveBeenCalled();
		hidden = false;
		emit('visibilitychange');
		expect(poll).toHaveBeenCalledTimes(1);
		stop();
	});

	it('does not start when the tab is already hidden', () => {
		hidden = true;
		const poll = vi.fn();
		const stop = pollWhileVisible(1_000, poll);
		vi.advanceTimersByTime(5_000);
		expect(poll).not.toHaveBeenCalled();
		stop();
	});

	it('asks once when a return fires both visibility and focus', () => {
		const poll = vi.fn();
		const stop = pollWhileVisible(1_000, poll);
		hidden = true;
		emit('visibilitychange');
		hidden = false;
		emit('visibilitychange');
		emit('focus');
		expect(poll).toHaveBeenCalledTimes(1);
		stop();
	});

	it('asks again on the next return after the tab was hidden', () => {
		const poll = vi.fn();
		const stop = pollWhileVisible(1_000, poll);
		emit('focus');
		expect(poll).toHaveBeenCalledTimes(1);
		hidden = true;
		emit('visibilitychange');
		hidden = false;
		emit('visibilitychange');
		expect(poll).toHaveBeenCalledTimes(2);
		stop();
	});
});
