<script lang="ts">
	import { onMount, untrack, type Snippet } from 'svelte';
	import { fitScale, frameCaption } from './preview-devices.js';

	// ── A true viewport simulator ───────────────────────────────────────────────────────────────
	// The children render inside an <iframe> whose INTERNAL viewport is `width`×`height` CSS px,
	// visually scaled down to fit the host column. Media queries inside the iframe evaluate at the
	// SIMULATED width — a 1920px frame genuinely renders the desktop chrome, a 390px one the mobile
	// tab bar — which a same-document `overflow:hidden` box can never do (its media queries see the
	// hosting page's viewport, so a ~450px editor column always previewed the mobile layout).
	//
	// The iframe also replaces the old frame's two containment hacks for free: `position: fixed`
	// chrome (glass canvas, mobile nav) is contained by the iframe viewport itself (no
	// `transform: translateZ(0)` containing-block trick), and nothing inside can navigate or focus
	// the HOST app because it is a separate document.
	//
	// ── Scrollable, but not interactive ─────────────────────────────────────────────────────────
	// The old frame was `inert`, which killed scrolling along with activation. Here the content
	// scrolls naturally (wheel/touch/keyboard reach the iframe's own scroll containers) and
	// text selection works; what is suppressed is ACTIVATION: capture-phase `click`/`submit`
	// listeners on the iframe document preventDefault + stopPropagation everything, and `keydown`
	// does the same for Enter/Space on links/buttons. This is belt-and-braces on top of the shells'
	// `interactive={false}` stand-ins (which keep the app-mutating chrome out of the DOM entirely) —
	// it stops residual anchors in demo content navigating the iframe. chrome.test.ts +
	// preview-frame.test.ts pin both layers.
	//
	// ── How the content gets in (Svelte 5 portal technique) ─────────────────────────────────────
	// The snippet renders into a hidden "stage" div in the PARENT document, and on adoption the
	// stage node is appended into the iframe body — moved DOM keeps its reactivity, so the preview
	// stays live as the editor mutates branding. Styles are cloned from the parent <head> (Vite dev
	// injects <style> tags; prod builds emit <link rel="stylesheet"> — both are copied, and a link
	// is pinned to the ABSOLUTE url the host resolved rather than its relative attribute), plus any
	// `fontHrefs`. The stage stays hidden until those cloned links are live, so the frame never
	// paints a pass with no CSS at all. Client-only by construction: nothing renders during SSR
	// (the studio is browser-only, but the guard is clean — the host div simply stays empty until
	// mount).
	let {
		width,
		height,
		surfaceStyle,
		scheme,
		fontHrefs = [],
		label,
		children
	}: {
		/** Simulated viewport width in CSS px (see preview-devices.ts). */
		width: number;
		/** Simulated viewport height in CSS px. */
		height: number;
		/** Inline declarations for the iframe <body> — the brandingPreviewStyle(...) output. */
		surfaceStyle: string;
		/** Applied as the `dark` class on the iframe's documentElement, as the real portal does. */
		scheme: 'light' | 'dark';
		/** Extra stylesheet URLs (Google Fonts) loaded inside the frame. */
		fontHrefs?: string[];
		/** Accessible name for the frame (host group + iframe title). */
		label: string;
		children: Snippet;
	} = $props();

	let hostWidth = $state(0);
	const scale = $derived(fitScale(hostWidth, width));
	/** Whether the host has actually been measured. `hostWidth` is 0 both before the first
	 *  measurement and for a genuinely hidden host, and those want opposite treatment: one is
	 *  temporary, the other is not. */
	const measured = $derived(hostWidth > 0);

	let hostEl = $state<HTMLDivElement>();
	let iframeEl = $state<HTMLIFrameElement>();
	let stageEl = $state<HTMLDivElement>();
	let mounted = $state(false);
	/** The iframe's document once adopted — the reactive hook the scheme/surface/font effects key off. */
	let frameDoc = $state<Document | null>(null);

	onMount(() => (mounted = true));

	/**
	 * A direct measurement, once, after mount — the belt to `bind:clientWidth`'s braces.
	 *
	 * THE DEFECT. `fitScale` collapses to 0 for an unmeasured host, deliberately, so the frame
	 * never flashes at full size before it is scaled down. The host's reserved height is
	 * `height * scale`, so an unmeasured host is a box 0px tall — the preview is not merely
	 * unscaled, it is GONE. That is correct for the one frame before the ResizeObserver behind
	 * `bind:clientWidth` reports, and it is what you are left looking at if that report never
	 * comes: navigating to this page client-side lands in an already-painted document where the
	 * observer's initial callback can be missed, and the preview stays a collapsed strip until
	 * something else — a window resize — happens to trigger it. A reload always looked fine,
	 * which is what made it read as "broken on first load".
	 *
	 * Reading `clientWidth` here cannot miss: it is a synchronous measurement of an element that
	 * is already in the document. It only ever fills in a first value — every later change is the
	 * observer's, exactly as before — and it writes nothing when the host genuinely has no width
	 * (a hidden container), so the deliberate collapse survives for the case it was written for.
	 */
	onMount(() => {
		if (hostWidth === 0 && hostEl) hostWidth = hostEl.clientWidth;
	});

	/**
	 * Mirror the parent head's styles into the iframe head. Clones are keyed by their source node
	 * so the sync is idempotent: new parent styles are cloned in, removed ones are cloned out, and
	 * a <style> whose TEXT changed (Vite HMR rewrites the tag's content in place in dev) has its
	 * clone's text refreshed. Links are cloned once — their href never mutates in place.
	 */
	function syncHeadStyles(doc: Document, cloneOf: Map<Element, Element>) {
		const sources = document.head.querySelectorAll('style, link[rel="stylesheet"]');
		const live = new Set<Element>(sources);
		for (const src of sources) {
			const clone = cloneOf.get(src);
			if (!clone) {
				const next = src.cloneNode(true) as Element;
				// THE DEFECT, in production. `cloneNode` copies the raw href ATTRIBUTE, and
				// SvelteKit writes those RELATIVE (`paths.relative` is its default, which is why
				// the built head says `href="./_app/…"`). A relative href is re-resolved against
				// wherever its document is NOW, and a client-side navigation moves the studio
				// without re-fetching anything: land on /tenants, click through to
				// /tenants/{uid}/branding, and the host's already-loaded `./_app/…` now reads as
				// /tenants/_app/… — harmless for the host, a 404 for a clone made after the move,
				// which leaves the preview with NO css at all until the page is reloaded.
				// `link.href` is no help: it is that same re-resolved url. `sheet.href` is the one
				// the host actually fetched, fixed when the stylesheet loaded, so that is what the
				// frame asks for and nothing has to agree about where "here" is.
				const fetched = (src as HTMLLinkElement).sheet?.href;
				if (fetched && next instanceof HTMLLinkElement) next.href = fetched;
				doc.head.appendChild(next);
				cloneOf.set(src, next);
			} else if (src.tagName === 'STYLE' && clone.textContent !== src.textContent) {
				clone.textContent = src.textContent;
			}
		}
		for (const [src, clone] of cloneOf) {
			if (!live.has(src)) {
				clone.remove();
				cloneOf.delete(src);
			}
		}
	}

	// Adoption: initialise the iframe document, move the stage in, install the suppression
	// listeners and the (dev-serving) style MutationObserver. Runs once both elements exist;
	// re-runs via the `load` listener because Firefox can asynchronously replace an iframe's
	// initial about:blank document — `adopt` is written to be safely re-runnable against the
	// replacement (appendChild auto-adopts nodes across documents).
	$effect(() => {
		const iframe = iframeEl;
		const stage = stageEl;
		if (!iframe || !stage) return;

		let observer: MutationObserver | null = null;
		let detachSuppressors: (() => void) | null = null;
		let revealTimer: ReturnType<typeof setTimeout> | undefined;

		/**
		 * Uncover the stage only once the frame's stylesheets can actually style it.
		 *
		 * A cloned <style> carries its rules with it and is live the instant it is inserted, so in
		 * dev — where Vite injects every stylesheet as a <style> — this is a no-op. A cloned <link>
		 * is a FETCH: production's stylesheets are links, the stage is already in the document while
		 * they are still in flight, and the preview therefore painted once with no CSS — a 1920px
		 * unstyled column of nav text and a logo at its natural size — before snapping into place.
		 * That flash is the same defect as the 404 above, just the version that recovers on its own.
		 *
		 * The timeout is the floor: a stylesheet that never answers must leave a preview that is
		 * late, not one that is blank for ever.
		 */
		const reveal = (doc: Document) => {
			const show = () => (stage.style.display = 'contents');
			const pending = [
				...doc.head.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')
			].filter((link) => !link.sheet);
			if (pending.length === 0) return show();
			stage.style.display = 'none';
			let waiting = pending.length;
			const settle = () => --waiting === 0 && show();
			for (const link of pending) {
				link.addEventListener('load', settle, { once: true });
				link.addEventListener('error', settle, { once: true });
			}
			clearTimeout(revealTimer);
			revealTimer = setTimeout(show, 2000);
		};

		const adopt = () => {
			const doc = iframe.contentDocument;
			// untrack: frameDoc is written below; reading it reactively here would make this
			// effect re-run on its own write, tearing down the listeners it just installed.
			if (!doc?.body || doc === untrack(() => frameDoc)) return;

			// Relative URLs in cloned styles (font files, images) must resolve against the HOST
			// page, not about:blank. Most browsers inherit the creator's base URL; the explicit
			// <base> makes it a guarantee rather than a behaviour.
			const base = doc.createElement('base');
			base.href = document.baseURI;
			doc.head.appendChild(base);

			const cloneOf = new Map<Element, Element>();
			syncHeadStyles(doc, cloneOf);
			observer?.disconnect();
			// Dev-only in effect: in prod the head's stylesheets are static after load, so this
			// observer simply never fires. In dev it follows Vite's HMR style churn (new <style>
			// tags and in-place text rewrites) so the preview keeps tracking the app's CSS.
			observer = new MutationObserver(() => syncHeadStyles(doc, cloneOf));
			observer.observe(document.head, { childList: true, subtree: true, characterData: true });

			// Activation suppression (capture phase, so it beats every handler inside the frame).
			// Scrolling, hover and text selection are untouched — only clicks, submits and
			// keyboard activation of links/buttons are neutered.
			const suppress = (e: Event) => {
				e.preventDefault();
				e.stopPropagation();
			};
			const suppressKeys = (e: KeyboardEvent) => {
				if (e.key !== 'Enter' && e.key !== ' ') return;
				const target = e.target as HTMLElement | null;
				if (target?.closest('a, button')) suppress(e);
			};
			detachSuppressors?.();
			doc.addEventListener('click', suppress, true);
			doc.addEventListener('submit', suppress, true);
			doc.addEventListener('keydown', suppressKeys, true);
			detachSuppressors = () => {
				doc.removeEventListener('click', suppress, true);
				doc.removeEventListener('submit', suppress, true);
				doc.removeEventListener('keydown', suppressKeys, true);
			};

			// Move the live stage into the frame. The nodes keep their Svelte reactivity —
			// they are the same nodes, just re-parented into the iframe's body.
			doc.body.appendChild(stage);
			reveal(doc);
			frameDoc = doc;
		};

		adopt();
		iframe.addEventListener('load', adopt);
		return () => {
			iframe.removeEventListener('load', adopt);
			observer?.disconnect();
			detachSuppressors?.();
			clearTimeout(revealTimer);
			frameDoc = null;
		};
	});

	// Scheme: the real portal toggles `.dark` on <html>; the frame does exactly the same to ITS
	// documentElement so the copied theme CSS (`.dark { … }` scopes) applies identically.
	$effect(() => {
		const doc = frameDoc;
		if (!doc) return;
		doc.documentElement.classList.toggle('dark', scheme === 'dark');
	});

	// Surface: the tenant's palette/shared tokens land inline on the iframe <body> — the same
	// brandingPreviewStyle string the old frame put on its wrapper div. `font-family` is declared
	// here too (not just the --font-sans var): the copied Tailwind preflight sets font-family on
	// the iframe's <html>, where a var overridden on <body> never reaches — same trap the old
	// frame's scoped rule existed for.
	$effect(() => {
		const doc = frameDoc;
		if (!doc) return;
		doc.body.style.cssText = `margin:0;font-family:var(--font-sans);${surfaceStyle}`;
	});

	// Fonts: naming a family in the surface vars without loading the file leaves the frame on the
	// fallback stack — mirror the editor's Google Fonts links into the frame head. Stale links are
	// left in place (harmless, cached) so toggling between fonts doesn't refetch.
	$effect(() => {
		const doc = frameDoc;
		const hrefs = fontHrefs;
		if (!doc) return;
		const have = new Set(
			Array.from(doc.head.querySelectorAll('link[data-preview-font]')).map((l) =>
				l.getAttribute('href')
			)
		);
		for (const href of hrefs) {
			if (have.has(href)) continue;
			const link = doc.createElement('link');
			link.rel = 'stylesheet';
			link.href = href;
			link.setAttribute('data-preview-font', '');
			doc.head.appendChild(link);
		}
	});
</script>

<div class="space-y-1.5">
	<!-- The host reserves the SCALED height so the page's layout is stable while the iframe keeps
	     its full simulated size and is transform-scaled into the box. role="group" (not the old
	     role="img"): the frame is scrollable content now, not a flat picture. -->
	<!-- `min-height` while unmeasured: the reserved height is `height * scale` and scale is 0
	     until the host is measured, so without a floor the box collapses to nothing and the page
	     jumps when it appears. A floor keeps the space the preview is about to occupy. -->
	<div
		bind:this={hostEl}
		bind:clientWidth={hostWidth}
		role="group"
		aria-label={label}
		class="bg-background overflow-hidden rounded-xl border shadow-sm"
		style="height: {Math.round(height * scale)}px{measured ? '' : '; min-height: 12rem'}"
	>
		{#if mounted}
			<iframe
				bind:this={iframeEl}
				title={label}
				class="block border-0"
				style="width: {width}px; height: {height}px; transform: scale({scale}); transform-origin: top left;"
			></iframe>
		{/if}
	</div>
	<p class="text-muted-foreground text-right text-xs tabular-nums">
		{frameCaption(width, height, scale)}
	</p>
</div>

{#if mounted}
	<!-- The stage: rendered hidden in the parent document, then re-parented into the iframe body
	     by the adoption effect (display:none until it lands, so fixed-position shell chrome never
	     flashes over the host page). -->
	<div bind:this={stageEl} style="display: none">
		{@render children()}
	</div>
{/if}
