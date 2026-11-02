import { afterEach, describe, expect, it, vi } from 'vitest';
import { watchLayout } from './layout-watch';

afterEach(() => {
	vi.restoreAllMocks();
	vi.useRealTimers();
	document.body.innerHTML = '';
});

describe('watchLayout', () => {
	it('runs after the debounce, on a body mutation, on a stylesheet swap, and cleans up on stop', async () => {
		vi.useFakeTimers();
		vi.spyOn(window, 'requestAnimationFrame').mockImplementation(
			(cb) => (cb(0), 1) as unknown as number
		);
		const root = document.createElement('div');
		document.body.append(root);
		const runs: HTMLElement[] = [];
		const cleanup = vi.fn();
		const stop = watchLayout(root, { run: (el) => runs.push(el), cleanup, debounceMs: 50 });
		expect(runs).toEqual([]);

		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toEqual([root]);

		root.append(document.createElement('p'));
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toHaveLength(2);

		// Vite's CSS hot reload replaces a <style> in the head: the layout changed, the body did not.
		const style = document.createElement('style');
		document.head.append(style);
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toHaveLength(3);

		// Many changes inside the window collapse into one run.
		root.append(document.createElement('p'));
		await vi.advanceTimersByTimeAsync(20);
		root.append(document.createElement('p'));
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toHaveLength(4);

		stop();
		expect(cleanup).toHaveBeenCalledWith(root);
		root.append(document.createElement('p'));
		await vi.advanceTimersByTimeAsync(60);
		expect(runs).toHaveLength(4);
		style.remove();
	});
});
