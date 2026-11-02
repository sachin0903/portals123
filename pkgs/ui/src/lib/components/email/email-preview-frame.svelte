<script lang="ts">
	/**
	 * The one way this product renders an email body on screen.
	 *
	 * ── Why the sandbox IS this component ───────────────────────────────────────────────────────
	 * `sandbox=""` — no `allow-scripts`, no `allow-same-origin`: the email HTML renders with
	 * scripting and same-origin access disabled. The body is author-controlled and is deliberately
	 * NOT sanitised server-side (the API's job is to merge the tokens and inline the CSS, not to
	 * police the markup), so an un-sandboxed `srcdoc` frame would execute injected script in the
	 * app's own origin — i.e. under the signed-in staff user's session and cookies.
	 *
	 * Do NOT add `allow-scripts` or `allow-same-origin` for any reason. Either alone re-opens that
	 * hole, and the two together are equivalent to deleting the sandbox. Real email clients don't
	 * run script either, so nothing a recipient will ever see needs it. Pinned by
	 * email-preview-frame.test.ts.
	 *
	 * It exists as a component because the incantation was on its way to three hand-copied call
	 * sites (the preview modal, the editor's live pane, list thumbnails) and a copy-pasted sandbox
	 * attribute is precisely the kind of thing that grows an `allow-scripts` in copy three.
	 *
	 * ── Geometry ────────────────────────────────────────────────────────────────────────────────
	 * The iframe's INTERNAL viewport is `width` CSS px, which is what makes the preview honest: an
	 * email's own media queries and `max-width` table wrappers evaluate at the device width, not at
	 * whatever column the preview happens to sit in. `scale` shrinks the rendered result visually
	 * (transform, top-left) without changing that viewport — so a 0.25 thumbnail is the same
	 * 640px-wide layout, just small. The host box reserves the SCALED size (`width * scale` wide,
	 * the caller's height) so page layout is stable, and clips any subpixel bleed.
	 */
	import { cn } from '../../utils.js';
	import { asPreviewDocument } from './as-preview-document.js';

	let {
		html,
		width = 640,
		scale = 1,
		title,
		fill = false,
		class: className
	}: {
		/** The server-rendered email body: merge fields already filled, CSS already inlined. */
		html: string;
		/** Device-frame width in CSS px — the iframe's internal viewport (640 desktop, 375 mobile). */
		width?: number;
		/**
		 * Visual scale. Below 1 the frame is a non-interactive thumbnail: scaled from the top-left
		 * and `pointer-events: none`, so it can't be scrolled, clicked or tabbed into.
		 */
		scale?: number;
		/** Accessible name for the frame. Required — an untitled iframe is an a11y failure. */
		title: string;
		/**
		 * Fill the parent instead of a device-width box. The parent must give a height; the email
		 * then scrolls INSIDE the frame rather than growing the page. A fragment is wrapped in a
		 * doctype (`asPreviewDocument`) so the card's max-width still holds at this size.
		 */
		fill?: boolean;
		/** Styles the framed BOX (height, border, radius, background); the iframe fills it. */
		class?: string;
	} = $props();

	/**
	 * The iframe's own box is `1 / scale` of the host in both axes, so scaling it by `scale` lands it
	 * exactly on the host's 100% × 100%. Percentages (not `{width}px`) so a host narrowed by
	 * `max-width: 100%` reflows the email rather than cropping it.
	 */
	const extent = $derived(`${100 / scale}%`);
	const thumbnail = $derived(
		scale === 1
			? ''
			: ` transform: scale(${scale}); transform-origin: top left; pointer-events: none;`
	);
</script>

<div
	class={cn('overflow-hidden', fill && 'h-full min-h-0 w-full', className)}
	style={fill ? undefined : `width: ${Math.round(width * scale)}px; max-width: 100%;`}
>
	<!-- sandbox="" (no allow-scripts, no allow-same-origin) — see the header comment. -->
	<iframe
		{title}
		srcdoc={asPreviewDocument(html)}
		sandbox=""
		class="block border-0"
		style="width: {extent}; height: {extent};{thumbnail}"
	></iframe>
</div>
