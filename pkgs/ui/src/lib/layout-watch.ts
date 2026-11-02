/**
 * Run a layout check now and again whenever the page changes, debounced to the next frame
 * after things settle. The overflow audit and the touch-target audit both hang off this —
 * one observer set, one frame, every check — so adding a third costs nothing but its walk.
 *
 * Development only: the checks read every element's computed style, and `MutationObserver`
 * on the whole body is not something to ship.
 */

export interface LayoutWatchOptions {
	/** The check. Called with the watched root after each settle. */
	run: (root: HTMLElement) => void;
	/** Undo any marks the check left, when the watch stops. */
	cleanup?: (root: HTMLElement) => void;
	/** Settle time after the last change before running. Default 250ms. */
	debounceMs?: number;
}

export function watchLayout(root: HTMLElement, options: LayoutWatchOptions): () => void {
	const wait = options.debounceMs ?? 250;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let frame: number | undefined;

	const run = () => {
		frame = undefined;
		options.run(root);
	};
	const schedule = () => {
		clearTimeout(timer);
		timer = setTimeout(() => {
			if (frame !== undefined) cancelAnimationFrame(frame);
			frame = requestAnimationFrame(run);
		}, wait);
	};

	const mutations = new MutationObserver(schedule);
	mutations.observe(root, {
		subtree: true,
		childList: true,
		characterData: true,
		attributes: true,
		// The checks' own `data-*` marks must not re-trigger a run.
		attributeFilter: ['class', 'style']
	});
	// A stylesheet swap (Vite's CSS hot reload) changes the layout without touching the body.
	if (root.ownerDocument.head && !root.contains(root.ownerDocument.head)) {
		mutations.observe(root.ownerDocument.head, {
			subtree: true,
			childList: true,
			characterData: true
		});
	}
	const resizes = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(schedule) : null;
	resizes?.observe(root);
	window.addEventListener('resize', schedule);
	schedule();

	return () => {
		clearTimeout(timer);
		if (frame !== undefined) cancelAnimationFrame(frame);
		mutations.disconnect();
		resizes?.disconnect();
		window.removeEventListener('resize', schedule);
		options.cleanup?.(root);
	};
}
