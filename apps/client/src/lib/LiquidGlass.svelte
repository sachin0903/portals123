<script lang="ts">
	import { browser } from '$app/environment';
	import { onDestroy, onMount } from 'svelte';

	let {
		/** CSS selector for the element(s) to turn into liquid glass. */
		target,
		/** Refraction strength (0–1). Higher = more edge bend. */
		refraction = 0.04,
		/** Magnification of the refracted backdrop (1 = none). */
		magnify = 1.06,
		bevelDepth = 0.12,
		bevelWidth = 0.15,
		/** Extra frost (blur) in px over the refraction. */
		frost = 3,
		specular = true,
		tilt = true,
		tiltFactor = 4
	}: {
		target: string;
		refraction?: number;
		magnify?: number;
		bevelDepth?: number;
		bevelWidth?: number;
		frost?: number;
		specular?: boolean;
		tilt?: boolean;
		tiltFactor?: number;
	} = $props();

	let started = false;
	/** The safety-net timers below; cleared on destroy so a fast sign-in (form card gone, page
	 *  navigated) does not leave three callbacks querying a document they no longer belong to. */
	let timers: ReturnType<typeof setTimeout>[] = [];

	onMount(async () => {
		if (!browser) return;
		// Respect reduced-motion: leave the CSS `.glass` fallback in place.
		if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

		try {
			// liquidGL reads a global `html2canvas`; attach it before init.
			const html2canvas = (await import('html2canvas')).default;
			window.html2canvas = html2canvas;
			// Side-effect import: the vendored IIFE attaches `window.liquidGL`.
			await import('$lib/vendor/liquidGL.js');

			if (typeof window.liquidGL !== 'function') return;

			// Wait a frame so the target is laid out (fonts, flex centring) before
			// html2canvas snapshots the backdrop.
			await new Promise((r) => requestAnimationFrame(() => r(null)));

			window.liquidGL({
				target,
				refraction,
				magnify,
				bevelDepth,
				bevelWidth,
				frost,
				specular,
				tilt,
				tiltFactor,
				shadow: true,
				// Instant reveal: liquidGL force-sets the target to opacity:0 and only
				// restores it on reveal. 'fade' makes that depend on a timed animation —
				// too risky for a login form. 'none' restores opacity as soon as ready.
				reveal: 'none'
			});
			// liquidGL disables pointer events on the target (it's built for decorative
			// panes). Our target IS the form card, so restore interactivity — the render
			// loop never touches pointer-events again, so this sticks.
			restoreInteractivity();
			started = true;
		} catch (e) {
			// Any failure (no WebGL, snapshot error) just leaves the CSS glass intact.
			console.warn('[liquidGL] init skipped:', e);
		}

		// Safety net: liquidGL sets the target opacity:0 + pointer-events:none up front
		// and only restores opacity when its snapshot/reveal completes. html2canvas is
		// brittle (it predates oklch/color-mix, which this theme uses), so guarantee the
		// form is always visible AND clickable — poll briefly, then hard-restore.
		timers = [400, 800, 1200].map((t) => setTimeout(restoreInteractivity, t));
	});

	function restoreInteractivity() {
		document.querySelectorAll<HTMLElement>(target).forEach((el) => {
			if (getComputedStyle(el).opacity === '0') el.style.opacity = '1';
			el.style.pointerEvents = 'auto';
		});
	}

	onDestroy(() => {
		for (const t of timers) clearTimeout(t);
		timers = [];
		if (!browser || !started) return;
		// liquidGL has no teardown API: it keeps a window-singleton renderer with a
		// fixed full-screen canvas + a rAF loop. Tear it down so it doesn't linger
		// (and keep rendering) on the pages we navigate to after login.
		const r = window.__liquidGLRenderer__;
		if (r) {
			if (r._rafId) cancelAnimationFrame(r._rafId);
			r.canvas?.remove();
			window.__liquidGLRenderer__ = undefined;
		}
	});
</script>
