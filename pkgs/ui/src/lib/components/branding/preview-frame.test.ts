import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Invariants of the branding preview's viewport-simulator frame.
 *
 * The frame's real behaviour — media queries evaluating at the simulated width, wheel scrolling,
 * style adoption into the iframe document — needs a REAL browser: jsdom's iframes don't run
 * layout, don't evaluate media queries and this package's vitest runs in a plain node
 * environment anyway. That behaviour is verified manually in the admin studio
 * (/tenants/{id}/branding); what CAN be pinned headlessly is the source shape of the safety and
 * fidelity contract, same technique as shell/chrome.test.ts. The pure scale maths is unit-tested
 * for real in preview-devices.test.ts.
 */
const here = import.meta.dirname;

/** Strip comments — HTML, block AND `//` line comments (the `:` lookbehind spares `https://`) —
 * so prose describing a hazard doesn't satisfy (or trip) a match. */
const stripComments = (code: string) =>
	code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(?<!:)\/\/[^\n]*/g, '');
const frame = stripComments(readFileSync(join(here, 'preview-frame.svelte'), 'utf8'));

describe('the frame is a real viewport, not a cropped box', () => {
	it('renders the content inside an iframe sized to the simulated device', () => {
		// The whole point of the component: media queries must evaluate at the DEVICE width, which
		// only a separate browsing context can do. A styled <div> here would regress the desktop
		// preview back to the squeezed mobile layout.
		expect(frame).toMatch(/<iframe/);
		expect(frame).toMatch(/width:\s*\{width\}px;\s*height:\s*\{height\}px/);
	});

	it('scales visually with a top-left transform and reserves the SCALED height', () => {
		// transform, not width/height shrinking — shrinking would change the internal viewport and
		// defeat the simulation. The host reserves height * scale so the page layout is stable.
		expect(frame).toMatch(/transform:\s*scale\(\{scale\}\);\s*transform-origin:\s*top left/);
		expect(frame).toMatch(/height:\s*\{Math\.round\(height \* scale\)\}px/);
		expect(frame).toMatch(/fitScale\(hostWidth,\s*width\)/);
		expect(frame).toMatch(/bind:clientWidth=\{hostWidth\}/);
	});

	it('mirrors both dev <style> tags and prod <link> stylesheets into the iframe head', () => {
		// Vite dev injects component CSS as <style>; production builds emit <link rel="stylesheet">.
		// Copying only one of the two makes the preview unstyled in the other environment — the kind
		// of thing that ships because dev looked fine.
		expect(frame).toMatch(/querySelectorAll\('style, link\[rel="stylesheet"\]'\)/);
		// And keeps tracking dev HMR churn (new tags + in-place text rewrites).
		expect(frame).toMatch(/new MutationObserver/);
		expect(frame).toMatch(/characterData:\s*true/);
	});

	it('applies the scheme exactly as the real portal does — `dark` on the documentElement', () => {
		expect(frame).toMatch(/documentElement\.classList\.toggle\('dark',\s*scheme === 'dark'\)/);
	});

	it('declares the body font-family itself, not only the --font-sans var', () => {
		// The copied Tailwind preflight sets font-family on the iframe <html>; a var overridden on
		// <body> never reaches a declaration that resolved higher up. Same trap the old frame's
		// scoped `.preview-frame` rule existed for.
		expect(frame).toMatch(/font-family:var\(--font-sans\)/);
	});

	it('renders nothing during SSR — iframe and stage are mount-gated', () => {
		expect(frame).toMatch(/\{#if mounted\}[\s\S]*<iframe/);
		expect(frame).toMatch(/\{#if mounted\}[\s\S]*bind:this=\{stageEl\}/);
	});
});

describe('scrollable, but not interactive', () => {
	it('does not use inert — inert is what made the old preview unscrollable', () => {
		expect(frame).not.toMatch(/\binert\b/);
	});

	it('suppresses activation with capture-phase listeners on the iframe document', () => {
		// Capture phase so the suppressor beats every handler inside the frame; click AND submit
		// always, keyboard activation (Enter/Space) only when it lands on a link/button — plain
		// typing and scroll keys stay untouched.
		expect(frame).toMatch(/addEventListener\('click',\s*suppress,\s*true\)/);
		expect(frame).toMatch(/addEventListener\('submit',\s*suppress,\s*true\)/);
		expect(frame).toMatch(/addEventListener\('keydown',\s*suppressKeys,\s*true\)/);
		expect(frame).toMatch(/e\.preventDefault\(\);\s*e\.stopPropagation\(\)/);
		expect(frame).toMatch(/e\.key !== 'Enter' && e\.key !== ' '/);
		expect(frame).toMatch(/closest\('a, button'\)/);
	});

	it('stays reachable to assistive tech as a labelled group, not a flat image', () => {
		// role="img" (the old frame) would hide the now-scrollable content; the iframe's title and
		// the host group's label both carry the accessible name.
		expect(frame).toMatch(/role="group"/);
		expect(frame).toMatch(/aria-label=\{label\}/);
		expect(frame).toMatch(/title=\{label\}/);
	});
});

/**
 * The frame must never be left as a 0px strip.
 *
 * THE DEFECT. `fitScale` collapses to 0 for an unmeasured host — deliberately, so the frame never
 * flashes full-size before being scaled down — and the host's reserved height is `height * scale`.
 * An unmeasured host is therefore a box 0px tall: the preview is not merely unscaled, it is gone.
 * That is right for the one frame before `bind:clientWidth`'s ResizeObserver reports, and it is
 * what you are left looking at if that report never comes — which is what navigating to the page
 * client-side could produce, while a reload always looked fine.
 *
 * Source-shape assertions, like the rest of this file: the real behaviour needs a browser with
 * layout, and this package's vitest runs in node.
 */
describe('an unmeasured host does not collapse the frame', () => {
	it('measures the host directly on mount as well as observing it', () => {
		// The belt to `bind:clientWidth`'s braces. A synchronous read of an element already in the
		// document cannot be missed the way an observer's first callback can.
		expect(frame).toContain('hostEl.clientWidth');
		expect(frame).toContain('bind:this={hostEl}');
	});

	it('only ever fills in a FIRST value, so the observer still owns every change', () => {
		// Writing unconditionally would fight the observer on every resize, and would also
		// overwrite the deliberate 0 of a genuinely hidden host.
		expect(frame).toMatch(/hostWidth === 0 && hostEl/);
	});

	it('reserves a floor while unmeasured, so the box holds its place', () => {
		expect(frame).toContain('min-height');
		expect(frame).toContain('measured');
	});

	it('still collapses rather than flashing once measured', () => {
		// The no-flash intent is unchanged: the floor applies ONLY while unmeasured, and the scale
		// maths is untouched (pinned for real in preview-devices.test.ts).
		expect(frame).toMatch(/measured \? '' :/);
	});
});

/**
 * The frame must never paint a pass with no CSS.
 *
 * THE DEFECT, in production, where the head's stylesheets are <link>s rather than dev's <style>
 * tags. Two halves, one cause — a link is a url that the document it sits in resolves, and a fetch
 * that the document it sits in has to finish:
 *
 *  1. `cloneNode` copies the raw HREF ATTRIBUTE, and SvelteKit writes those relative
 *     (`paths.relative` is its default). A relative href re-resolves against wherever its document
 *     is NOW, and a client-side navigation moves the studio without re-fetching: land on /tenants,
 *     click through to /tenants/{uid}/branding, and `./_app/…` reads as /tenants/_app/… — harmless
 *     for the host, a 404 for a clone made after the move, and a preview with no CSS at all until
 *     the page is reloaded. `link.href` is that same re-resolved url; `sheet.href` is the one the
 *     host actually fetched.
 *  2. Even when the url is right, the clone must FETCH it, and the stage is already in the frame
 *     while it is in flight — so the preview painted unstyled first and snapped into place after.
 *
 * Source-shape assertions, like the rest of this file.
 */
describe('the frame is styled before it is seen', () => {
	it('pins a cloned stylesheet to the absolute url the host resolved', () => {
		expect(frame).toMatch(/\(src as HTMLLinkElement\)\.sheet\?\.href/);
		expect(frame).toMatch(/next instanceof HTMLLinkElement\) next\.href = fetched/);
	});

	it('keeps the stage hidden until the cloned stylesheets are live', () => {
		// `link.sheet` is null while a stylesheet is pending — the frame waits on exactly those,
		// and on nothing at all in dev, where every stylesheet clone is a <style> and already live.
		expect(frame).toMatch(/\.filter\(\(link\) => !link\.sheet\)/);
		expect(frame).toMatch(/addEventListener\('load', settle/);
		expect(frame).toMatch(/addEventListener\('error', settle/);
		expect(frame).toMatch(/display = 'none'/);
	});

	it('reveals late rather than never when a stylesheet does not answer', () => {
		expect(frame).toMatch(/revealTimer = setTimeout\(show/);
		expect(frame).toMatch(/clearTimeout\(revealTimer\)/);
	});
});
