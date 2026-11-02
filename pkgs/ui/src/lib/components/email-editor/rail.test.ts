import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/** Two `top: 0` stickies in one scroller collide, so this counts sticky-classed elements per scroller. */
const source = readFileSync(join(import.meta.dirname, 'email-editor.svelte'), 'utf8');
const pageSource = readFileSync(
	join(import.meta.dirname, 'email-template-editor-page.svelte'),
	'utf8'
);

const styles = source.match(/<style>([\s\S]*)<\/style>/)?.[1] ?? '';

function stickyClasses(css: string): string[] {
	const found = new Set<string>();
	for (const rule of css.split('}')) {
		const [prelude, body = ''] = rule.split('{');
		if (!/position:\s*sticky/.test(body)) continue;
		for (const cls of prelude.match(/\.[a-zA-Z][\w-]*/g) ?? []) found.add(cls.slice(1));
	}
	return [...found];
}

function rails(): string[] {
	// `[^>]*` after the class: the element carries other attributes, and requiring `">` found no rail.
	const found = [...source.matchAll(/<aside class="rail[^"]*"[^>]*>([\s\S]*?)<\/aside>/g)].map(
		(m) => m[1] ?? ''
	);
	if (found.length === 0) throw new Error('no rail found in email-editor.svelte');
	return found;
}

const elementsWith = (markup: string, cls: string) =>
	(markup.match(new RegExp(`class="[^"]*\\b${cls}\\b[^"]*"`, 'g')) ?? []).length;

/** `text` minus its comments, for any ABSENCE assertion: prose that must QUOTE what it replaced trips a raw scan. */
const stripComments = (text: string): string =>
	text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/<!--[\s\S]*?-->/g, '');

describe('the rail’s sticky headers', () => {
	it('pins at most ONE header per scroll container', () => {
		// None at all now; `<= 1` rather than an equality, so one header is fine and two in a scroller is not.
		const sticky = stickyClasses(styles);
		const all = rails();
		all.forEach((markup, i) => {
			const total = sticky.reduce((n, cls) => n + elementsWith(markup, cls), 0);
			expect(total, `rail ${i} has ${total} sticky headers`).toBeLessThanOrEqual(1);
		});
	});

	it('names the selection nowhere in the rail', () => {
		// The stylesheet too: a dead `position: sticky` class keeps the count passing while the chrome returns.
		expect(styles).not.toContain('.rail-label');
		expect(source).not.toContain('class="rail-label"');
	});

	it('gives the section a content-sized box, so its sticky child stays pinned', () => {
		// Shrinkable, the section collapsed to the rail's height — 712px of a 2,409px section — and the header unpinned.
		expect(styles).toMatch(/\.rail-section\s*\{[^}]*flex:\s*none/);
		expect(styles).not.toMatch(/\.rail-section\s*\{[^}]*min-height:\s*0/);
		expect(styles).toMatch(/\.rail\s*\{[^}]*overflow-y:\s*auto/);
	});
});

describe('the rail is ONE accordion', () => {
	// Every band must be the SAME component: hand-repeated copies of a 200-character class string had drifted.
	const inspector =
		source.match(
			/\{#if !readOnly && \(linkTarget \|\| imageTarget\)\}([\s\S]*?<\/InspectorSector>)/
		)?.[1] ?? '';

	it('draws every band with the shared primitive, not a hand-rolled disclosure', () => {
		expect(inspector, 'the inspector block was not found').not.toBe('');
		expect(inspector).toContain('<InspectorSector');
		// A raw `<button>` loses keyboard access, and both field sets must sit inside the collapsing half.
		expect(inspector).not.toMatch(/<button/);
		const content = inspector.match(/>([\s\S]*)<\/InspectorSector>/)?.[1] ?? '';
		expect(content).toContain('id="et-href"');
		expect(content).toContain('id="et-alt"');
	});

	it('has no second copy of the sector header left in the rail', () => {
		// The class string lives once, in `ui/inspector-sector/inspector-sector.svelte`; a copy here drifts.
		expect(source).not.toContain('group-data-[state=closed]/sector:-rotate-90');
		expect(source).not.toContain('<Collapsible.Trigger');
	});

	it('offers a way back UP from a selection, and going back deselects', () => {
		// It must DESELECT rather than scroll: the page's own background and inset live in the panel's idle state.
		expect(source).toMatch(/\{#if selectedName\}\s*<button type="button" class="rail-back"/);
		expect(source).toMatch(/onclick=\{backToPageStyle\}/);
		const back = /function backToPageStyle\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		expect(back, 'backToPageStyle not found').not.toBe('');
		expect(back).toContain('editor?.select?.(null)');
	});

	it('still titles the panel rather than naming the selection', () => {
		// GrapesJS's own type names are the wrong vocabulary (`text` for a heading, `cell` for the page).
		expect(source).toContain("{selectedName ? 'Style' : 'Page style'}");
	});

	it('says which bands carry a value, and says it in the same way for all of them', () => {
		// `set` alone lights the dot, and Repeat stays separate from Visibility so one dot answers one question.
		expect(source).toMatch(
			/<InspectorSector\s+label="Repeat"\s+bind:open=\{repeatOpen\}\s+set=\{!!repeat\}\s*>/
		);
		expect(source).toMatch(
			/<InspectorSector\s+label="Visibility"\s+bind:open=\{visibilityOpen\}\s+set=\{!!visibility\}\s*>/
		);
		expect(source).toContain(
			'<InspectorSector label="Field" set={!!field.filter || !!field.fallback}>'
		);
		// Link / content-image alt / brand-logo wordmark — one sector, three answers for the dot.
		expect(source).toMatch(
			/set=\{linkTarget\s*\?\s*!!linkHref\s*:\s*isBrandLogo\(imageTarget\)\s*\?\s*!!imageFallback\?\.text\s*:\s*!!imageAlt\}/
		);
	});

	it('opens itself for a newly selected link or image, but leaves a re-sync alone', () => {
		// The only route to an href or an alt text, so a NEW selection opens it while a re-sync leaves a collapse alone.
		const refresh = source.match(/function refreshSelection\(\)[\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(refresh).toMatch(/if \(cid && cid !== inspectedCid\) inspectorOpen = true;/);
		expect(refresh).toContain('inspectedCid = cid;');
	});
});

/** The workspace is visibly assembling for most of a second. Asserted on SOURCE: `ready` never becomes true. */
describe('the loading cover', () => {
	const workspace = source.split('<!-- Workspace:')[1]?.split('<style>')[0] ?? '';

	it('covers the workspace until the editor is ready, and not after', () => {
		expect(workspace).toMatch(/\{#if !ready && !mountError\}/);
		expect(workspace).toContain('class="loading"');
		expect(styles).toMatch(/\.loading\s*\{[^}]*position:\s*absolute/);
		expect(styles).toMatch(/\.loading\s*\{[^}]*inset:\s*0/);
	});

	it('is opaque, not a scrim — a veil over a half-drawn page is still a half-drawn page', () => {
		const rule = /\.loading\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '';
		expect(rule).toMatch(/background:\s*var\(--[a-z-]+\)/);
		expect(rule).not.toMatch(/opacity|rgba|backdrop-filter/);
	});

	it('draws the sheet at the device width, so nothing moves at the reveal', () => {
		expect(styles).toMatch(/\.loading-sheet\s*\{[^}]*width:\s*var\(--device-w/);
		expect(workspace).toMatch(/style:--device-w=\{deviceWidth\(device\)\}/);
	});

	it('uses the shared Skeleton, and says so to a screen reader', () => {
		expect(source).toMatch(/import \{ Skeleton \} from '\.\.\/ui\/skeleton/);
		expect(workspace).toContain('<Skeleton');
		expect(workspace).toContain('aria-hidden="true"');
		expect(workspace).toMatch(/role="status"[\s\S]{0,80}Loading the email editor/);
	});
});

/** The panel FLOATS, so without `flex: 1` the backdrop stops short and it reads as stuck to the window. */
describe('the floating panel and the canvas under it', () => {
	it('fills the workspace, so the backdrop runs under the panel', () => {
		expect(styles).toMatch(/\.canvas-column\s*\{[^}]*flex:\s*1/);
		expect(styles).toMatch(/\.canvas-column\s*\{[^}]*background:\s*var\(--muted\)/);
	});

	it('keeps the email out from under the blocks rail, which is always there', () => {
		// 68px = the 20px base inset plus the 44px blocks rail at its own 12px inset, and the rail never goes away.
		expect(styles).toMatch(
			/\.canvas-column\s*\{[\s\S]*?padding-left:\s*calc\(68px \+ var\(--code-gutter/
		);
	});

	it('RESERVES NOTHING for the style panel, so the email is centred in the full width', () => {
		// TWO RULES AT ONCE: the panel must not MOVE the email and must leave it CENTRED, so the gutter is GONE.
		expect(styles).not.toContain('--rail-gutter:');
		expect(source).not.toMatch(/style:--rail-gutter=/);
		expect(stripComments(source)).not.toMatch(/railOpen \? '/);
		expect(styles).toMatch(/\.canvas-column\s*\{[\s\S]*?padding-right:\s*68px/);
	});

	it('paints the chrome a step OFF the email’s own surface', () => {
		// Three surfaces: sheet `--card` · chrome `--email-chrome` · backdrop `--muted`, the skeleton included.
		expect(styles).toMatch(
			/--email-chrome:\s*color-mix\(in oklab, var\(--card\) 97%, var\(--foreground\)\)/
		);
		// A MIX TOWARD `--foreground` is right in both themes (L 1.0 → 0.977 light, 0.22 → 0.242 dark): no branch.
		expect(styles).not.toMatch(/prefers-color-scheme[\s\S]{0,200}--email-chrome/);
		// `in oklab`, not `oklch`: `theme.css` records hue interpolation dragging a mixed neutral toward blue.
		expect(styles).not.toMatch(/--email-chrome:[^;]*oklch/);
		expect(styles).toMatch(/\.rail\s*\{[^}]*background:\s*var\(--email-chrome\)/);
		expect(styles).toMatch(/\.rail-reopen\s*\{[^}]*background:\s*var\(--email-chrome\)/);
		expect(styles).toMatch(/\.loading-rail\s*\{[^}]*background:\s*var\(--email-chrome\)/);
		expect(styles).toMatch(/\.loading-sheet\s*\{[^}]*background:\s*var\(--card\)/);
		expect(styles).toMatch(/\.rail-right\s*\{[^}]*box-shadow:\s*var\(--elev-2\)/);
		expect(styles).toMatch(/\.rail-right\s*\{[^}]*outline:\s*1px solid/);
	});

	it('states the panel’s width once, and its own skeleton matches it', () => {
		// It was 264px against a real 288, so the panel grew at the reveal; an equality makes a change move both.
		const rail = /\.rail-right\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '';
		const skeleton = /\.loading-rail\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '';
		const width = /width:\s*(\d+)px/.exec(rail)?.[1];
		expect(width).toBe('320');
		expect(/width:\s*(\d+)px/.exec(skeleton)?.[1]).toBe(width);
	});

	it('is positioned against the WORKSPACE, not the scroller', () => {
		expect(styles).toMatch(/\.rail-right\s*\{[^}]*position:\s*absolute/);
		expect(styles).toMatch(/\.workspace\s*\{[^}]*position:\s*relative/);
		// A child of the scroller would slide away with the email, and `overflow` here would kill `.rail`'s scroll.
		expect(/\.rail-right\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '').not.toMatch(/overflow/);
	});
});

/** THE SUBJECT BAND AND THE SHEET ARE ONE COLUMN — the head is a snippet OUTSIDE GrapesJS's frame. A
 *  frame-only resize gave a 375px email under a 640px header; a narrower box centres the head while the
 *  frame clamps left, a measured 101px apart. */
describe('the document head and the email frame', () => {
	it('takes its width from the device, so the toggle moves both', () => {
		expect(styles).toMatch(/\.gjs-email-head\s*\{[^}]*width:\s*var\(--device-w/);
		expect(source).toMatch(/style:--device-w=\{deviceWidth\(device\)\}/);
	});

	it('NEVER SHRINKS — it yields exactly the way the frame yields', () => {
		// Measured 946px workspace: head 532px against a 640px frame. NO CAP; auto margins go to zero instead.
		const rule = (/\.gjs-email-head\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '').replace(
			/\/\*[\s\S]*?\*\//g,
			''
		);
		expect(rule, 'the head rule was not found').not.toBe('');
		expect(rule).toMatch(/margin-inline:\s*auto/);
		expect(rule).not.toMatch(/max-width/);
		expect(rule).not.toMatch(/width:\s*(min|clamp)\(/);
		expect(rule).toMatch(/flex:\s*none/);
		// Comments stripped above: the note in that rule has to QUOTE `max-width: 100%` to explain why it is gone.
		expect(rule).not.toMatch(/(?<!max-)width:\s*100%/);
		expect(rule).not.toMatch(/transition/);
	});

	it('takes its width from the SAME value the frame is given', () => {
		expect(styles).toMatch(/\.gjs-email-head\s*\{[\s\S]*?width:\s*var\(--device-w/);
		expect(styles).toMatch(/\.canvas\s*\{[\s\S]*?width:\s*var\(--device-w/);
		expect(source).toMatch(/style:--device-w=\{deviceWidth\(device\)\}/);
	});

	it('gives the mount an EXACT width, not a floor', () => {
		// THE COLD-LOAD DEFECT: GrapesJS applies a device width to `.gjs-frame-wrapper` only when the device CHANGES,
		// so a fresh wrapper fills its container (800px against a declared 640px). A FLOOR CANNOT FIX IT.
		const canvas = (/\.canvas\s*\{([\s\S]*?)\n\t\}/.exec(styles)?.[1] ?? '').replace(
			/\/\*[\s\S]*?\*\//g,
			''
		);
		expect(canvas, 'the .canvas rule was not found').not.toBe('');
		// A MIN/MAX PAIR, NOT `width`: GrapesJS writes an inline `width: auto` here, which beats a stylesheet rule
		// (measured 0px). Neither bound is inline, so pinning both applies — and a bare `width` must not come back.
		expect(canvas).toMatch(/min-width:\s*var\(--device-w, 640px\)/);
		expect(canvas).toMatch(/max-width:\s*var\(--device-w, 640px\)/);
		expect(canvas).not.toMatch(/(?<!(min|max)-)width:\s*var/);
		expect(canvas).toMatch(/margin-inline:\s*auto/);
		expect(canvas).not.toMatch(/align-self/);
	});

	it('does not let a stale measured width outrank it', () => {
		// THE HEAD WAS ALWAYS ONE DEVICE BEHIND: `syncHeadWidth`'s INLINE width beats the stylesheet and its handler
		// read the wrapper at the old size, so `setDevice` clears it AFTER `editor.setDevice` (a synchronous emit).
		const fn =
			/function setDevice\(name: 'Desktop' \| 'Mobile portrait'\): void \{[\s\S]*?\n\t\}/.exec(
				source
			)?.[0];
		expect(fn, 'setDevice not found').toBeTruthy();
		expect(fn).toMatch(
			/editor\?\.setDevice\?\.\(name\);[\s\S]*if \(headEl\) headEl\.style\.width = '';/
		);
		expect(source).toMatch(/stopHeadMount = syncHeadWidth\(ed, headEl\)/);
	});

	it('gives the loading placeholder the same yielding rule', () => {
		const sheet = (/\.loading-sheet\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '').replace(
			/\/\*[\s\S]*?\*\//g,
			''
		);
		expect(sheet).toMatch(/margin-inline:\s*auto/);
		expect(sheet).not.toMatch(/max-width/);
	});

	it('has no yielding right-hand cap left to get wrong', () => {
		// An ABSENCE: a cap returning means something is reserved on the right again — the two-gutter squeeze.
		expect(styles).not.toMatch(/padding-right:\s*max\(/);
		expect(styles).not.toMatch(/padding-right:[\s\S]{0,80}min\(/);
	});

	/**
	 * THE CODE VIEW MOVES THE EMAIL; THE STYLE PANEL MUST NOT — pinned together, or a reader finding one will
	 * "fix" it to match the other. A style panel is ABOUT the selection; a code view is read ALONGSIDE it.
	 */
	describe('the HTML code pane', () => {
		const pane = readFileSync(join(import.meta.dirname, 'code-view/code-view.svelte'), 'utf8');
		const paneStyles = pane.match(/<style>([\s\S]*)<\/style>/)?.[1] ?? '';

		it('makes room through a class, not an inline gutter', () => {
			// The inline form is what went wrong on the style panel; in the stylesheet both padding sums sit together.
			expect(source).not.toMatch(/style:--code-gutter=/);
			expect(source).toContain('class:code-open={codeOpen}');
			expect(styles).toMatch(
				/\.workspace\.code-open\s*\{\s*--code-gutter:\s*calc\(var\(--code-w\) \+ 16px\)/
			);
			expect(styles).toMatch(/\.workspace\s*\{[\s\S]*?--code-gutter:\s*0px/);
		});

		it('yields its width so the email is never covered at a normal size', () => {
			// What is LEFT after the 640px email, both 68px insets, the air and the scrollbar; fixed, it hid it at 1440px.
			expect(styles).toMatch(
				/--code-w:\s*clamp\(\s*300px,\s*calc\(100% - 792px - var\(--scrollbar-allowance\)\),\s*900px\s*\)/
			);
			expect(paneStyles).toMatch(/\.code-pane\s*\{[^}]*width:\s*var\(--code-w/);
			expect(styles).toContain('--code-gutter: calc(var(--code-w) + 16px)');
		});

		it('lays out the real column and the loading placeholder by ONE sum', () => {
			expect([
				...styles.matchAll(/padding-left:\s*calc\(68px \+ var\(--code-gutter, 0px\)\)/g)
			]).toHaveLength(2);
			expect([...styles.matchAll(/padding-right:\s*68px/g)]).toHaveLength(2);
		});

		it('floats against the WORKSPACE, at the style panel’s own inset and elevation', () => {
			expect(paneStyles).toMatch(/\.code-pane\s*\{[^}]*position:\s*absolute/);
			expect(styles).toMatch(/\.workspace\s*\{[^}]*position:\s*relative/);
			expect(paneStyles).toMatch(/\.code-pane\s*\{[^}]*box-shadow:\s*var\(--elev-2\)/);
			expect(paneStyles).toMatch(/\.code-pane\s*\{[^}]*z-index:\s*20/);
		});

		it('SOFT-wraps its code, and clears the blocks rail', () => {
			// SOFT: `pre-wrap` paints one line over several rows and THE STRING IS UNTOUCHED. Never a hard wrap —
			// `format.ts` refuses a newline inside an element containing text, because it reaches the recipient.
			expect(paneStyles).toMatch(/\.code-text\s*\{[^}]*white-space:\s*pre-wrap/);
			expect(paneStyles).toMatch(/\.code-stack\s*\{[^}]*width:\s*100%/);
			expect(paneStyles).not.toMatch(/\.code-stack\s*\{[^}]*max-content/);
			// 12px inset + the 44px blocks rail + 8px of air, so the `</>` button that opened it stays clickable.
			expect(paneStyles).toMatch(/\.code-pane\s*\{[^}]*left:\s*64px/);
		});

		it('eases the email’s displacement, and never a width', () => {
			// The COLUMN'S PADDING is eased, so nothing changes its own dimensions; a `width` would diverge the two.
			expect(styles).toMatch(/\.canvas-column\s*\{[\s\S]*?transition:\s*padding-left\s+180ms/);
			const head = (/\.gjs-email-head\s*\{([^}]*)\}/.exec(styles)?.[1] ?? '').replace(
				/\/\*[\s\S]*?\*\//g,
				''
			);
			expect(head).not.toMatch(/transition/);
			expect(stripComments(styles)).not.toMatch(/transition:[^;]*width/);
			expect(styles).toMatch(
				/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.canvas-column\s*\{\s*transition:\s*none/
			);
			expect(paneStyles).toMatch(
				/@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.code-pane\s*\{\s*animation:\s*none/
			);
		});

		it('puts the style panel away, and puts back exactly what was there', () => {
			const toggle = /function toggleCode\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
			expect(toggle, 'toggleCode not found').not.toBe('');
			expect(toggle).toMatch(/codeOpen = false;\s*railOpen = railOpenBeforeCode;/);
			expect(toggle).toMatch(
				/railOpenBeforeCode = railOpen;\s*railOpen = false;\s*codeOpen = true;/
			);
			expect(source).toContain('oncode={toggleCode}');
			expect(source).toContain('onclose={toggleCode}');
			// The reopen TAB is withheld too: at the pane's own inset it would offer a panel that covers the code.
			expect(source).toContain('{:else if !codeOpen}');
		});

		it('does not let the panel’s state move the email, in either mode', () => {
			expect(styles).not.toContain('--rail-gutter:');
			expect(source).not.toMatch(/style:--rail-gutter=/);
			// `railOpen` appears in no layout DECLARATION. Comments stripped: the stylesheet's own notes name it.
			expect(stripComments(styles)).not.toContain('railOpen');
		});

		it('does not clip itself out of scrolling', () => {
			// `overflow: hidden` on the card kills the inner scroll. Comments stripped: the rule's own prose names it.
			expect(/\.code-pane\s*\{([^}]*)\}/.exec(stripComments(paneStyles))?.[1] ?? '').not.toMatch(
				/overflow/
			);
			expect(paneStyles).toMatch(/\.code-scroll\s*\{[^}]*overflow:\s*auto/);
			expect(paneStyles).toMatch(/\.code-scroll\s*\{[^}]*border-radius:\s*0 0 12px 12px/);
		});

		it('is offered to a read-only principal, but the unlock is not', () => {
			// Reading a template you may only read is real; read-only withholds `onapply`, the only route to replace.
			expect(source).toContain('onapply={readOnly ? undefined : applyCode}');
			expect(source).toContain('codeDisabled={!ready}');
			expect(source).toMatch(/\{#if codeOpen\}\s*<CodeView/);
		});

		it('applies a hand edit through the one seeding seam, with undo paused', () => {
			// A whole-document replacement pushes a dozen undo entries, and the confirm says it cannot be undone.
			const apply =
				/function applyCode\(next: string\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
			expect(apply, 'applyCode not found').not.toBe('');
			expect(apply).toContain('seed(');
			expect(apply).toContain('loadEmailContent(editor, { html: next, css: keptCss })');
			// The page cell must be re-wrapped: `setComponents` replaces it, losing drop-anywhere for the session.
			expect(apply).toContain('ensurePageCell(editor)');
			// The stylesheet is read BEFORE the replacement — the parse mints new ids.
			expect(apply.indexOf('getCss')).toBeLessThan(apply.indexOf('seed('));
			expect(apply).toContain('editor.select?.(null)');
			expect(apply).toContain('emitNow()');
		});

		it('opens on the SENT shape', () => {
			// "What will my clients receive" is the question; the editor shape's conditions are percent-encoded JSON.
			expect(source).toContain("let codeShape = $state<CodeShape>('sent')");
			expect(source).toMatch(/codeShape === 'sent' \? content\.html : content\.source/);
		});
	});
});

/** A SEED IS NOT AN UNDO STEP. One click of Starter layout measured FOURTEEN undo entries — ten from
 *  `ensurePageCell`'s re-wrap (fixed at source, see `page-cell.test.ts`), three from the replacement, one
 *  from the page inset. Both seeders now run inside `UndoManager.skip`, and both need an EMPTY canvas. */
describe('seeding the canvas', () => {
	it('runs both seeders with undo tracking paused', () => {
		// One helper, both call sites: a seeder with its own copy of the pause is one that can forget it.
		expect(source).toMatch(/function seed\(apply: \(\) => void\): void \{[\s\S]{0,200}?um\?\.skip/);
		const starter = /function insertStarter\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		const dflt = /function loadDefault\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		expect(starter, 'insertStarter not found').not.toBe('');
		expect(dflt, 'loadDefault not found').not.toBe('');
		expect(starter).toContain('seed(');
		expect(dflt).toContain('seed(');
	});

	it('keeps the page inset write inside the seed, not outside it', () => {
		// `setPageStyle` is ALSO the Page style panel's writer and must stay undoable there.
		const dflt = /function loadDefault\(\): void \{[\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		const inSeed = /seed\(\(\) => \{([\s\S]*?)\n\t\t\}\);/.exec(dflt)?.[1] ?? '';
		expect(inSeed, 'loadDefault has no seed block').not.toBe('');
		expect(inSeed).toContain("setPageInset(editor, '0')");
	});

	it('falls back to applying the seed when there is no UndoManager', () => {
		expect(source).toMatch(/if \(um\?\.skip\) um\.skip\(apply\);\s*else apply\(\);/);
	});
});

/** "UNSAVED" MEANS THE CONTENT DIFFERS FROM WHAT WAS SAVED, and the editor's output can never equal what
 *  was stored — 3055 characters in the database against 3310 from the canvas on password_reset, because
 *  `ensurePageCell` wraps in a page table and GrapesJS normalises. So the baseline is the first emission. */
describe('the unsaved flag', () => {
	it('measures against the editor’s own baseline, not the stored string', () => {
		expect(pageSource).toMatch(
			/let baseline = \$state<\{ html: string; css: string; source: string \} \| null>\(null\)/
		);
		expect(pageSource).toMatch(/dirty = false;[\s\S]{0,300}?baseline = null;/);
	});

	it('does not mark a sync that changed nothing', () => {
		const handler = /onChange=\{\(v\) => \{[\s\S]*?\n\t\t\t\}\}/.exec(pageSource)?.[0] ?? '';
		expect(handler, 'onChange not found').not.toBe('');
		expect(handler).toMatch(/v\.html === baseline\.html/);
		expect(handler).toMatch(/return; \/\/ a sync that changed nothing/);
		expect(handler).not.toMatch(/source = v\.source;\s*\n\s*dirty = true;/);
	});

	it('takes the FIRST settled emission after a load as the baseline', () => {
		// NO TIME BOUND: a `BASELINE_GRACE_MS` window makes a slowly-settling canvas read as unsaved, untouched.
		expect(pageSource).not.toContain('BASELINE_GRACE_MS');
		expect(pageSource).toMatch(/if \(!baseline\) \{[\s\S]{0,200}?baseline = \{ html: v\.html/);
	});

	it('treats a successful save as the new clean state', () => {
		// The clean state is what the EDITOR reports it saved, never the state trio — the trio lags the
		// 250ms sync debounce, so the next settled emission would compare against a pre-save baseline and
		// mark the template dirty again. A null reading (canvas not settled, so the stored strings were
		// posted) falls back to "the next settled emission is the baseline".
		expect(pageSource).toMatch(/dirty = false;[\s\S]{0,420}?baseline = c \?\? null/);
	});
});

/** PUBLISHING ASKS FIRST: it replaces the email every future send of this code goes out as, with no undo. */
describe('publishing', () => {
	it('goes through a confirmation rather than submitting directly', () => {
		// `type="button"`, so the dialog's confirm is what submits the form.
		expect(pageSource).toMatch(/onclick=\{\(\) => \(publishOpen = true\)\}/);
		expect(pageSource).toContain('bind:open={publishOpen}');
		expect(pageSource).toContain('title="Publish this email?"');
	});

	it('submits through requestSubmit, so enhance still runs', () => {
		// A bare `submit()` bypasses `use:enhance` and does a full page POST, losing the `onPublished` callback.
		expect(pageSource).toMatch(/function doPublish\(\): void \{[\s\S]*?requestSubmit\(\)/);
	});

	it('is NOT dressed as destructive', () => {
		const dlg = /<ConfirmDialog\s+bind:open=\{publishOpen\}[\s\S]*?\/>/.exec(pageSource)?.[0] ?? '';
		expect(dlg, 'publish dialog not found').not.toBe('');
		expect(dlg).not.toContain('destructive');
		expect(dlg).toContain('confirmLabel="Publish"');
	});
});

/** THE DEVICE TOGGLE PREVIEWS A WIDTH; IT DOES NOT SELECT A STYLE TARGET. `avoidInlineStyle: false` lands a
 *  narrowed style INLINE, at every width, and a `@media` fix cannot ship: `css_inline` keeps no at-rules. */
describe('the preview-width toggle', () => {
	it('names the group, not just the segments', () => {
		// A tablist with no accessible name reads as "Desktop, tab, 1 of 2", and the NAME carries the distinction.
		expect(source).toContain('label="Preview width"');
	});

	it('warns in the style panel while the preview is narrowed', () => {
		expect(source).toMatch(/\{#if selectedName && device !== 'Desktop'\}/);
		expect(source).toContain('Styles apply at every width');
	});
});

/** A flyout covering the canvas must be dismissable by clicking the thing it covers. */
describe('the rail flyouts close on a click outside them', () => {
	it('is held by the workspace, so a canvas click can reach it', () => {
		// The rail cannot see a click on the canvas: the canvas is an iframe, and the backdrop is the
		// workspace's own column — so the state lives here and the rail takes it bound.
		expect(source).toMatch(/let railFlyout = \$state<string \| null>\(null\)/);
		expect(source).toMatch(/bind:open=\{railFlyout\}/);
	});

	it('closes on the backdrop click, and NOT on a selection', () => {
		const backdrop = source.slice(source.indexOf('function deselectOnBackdrop'));
		expect(backdrop.slice(0, 400)).toContain('railFlyout = null');
		// `component:selected` is not a user gesture — it fires while the canvas settles, and on an
		// empty template it fired repeatedly, closing the flyout the instant it opened.
		const selected = source.slice(source.indexOf("ed.on('component:selected'"));
		expect(selected.slice(0, 300)).not.toContain('railFlyout = null');
	});

	it('closes on a CLICK, never a pointerdown', () => {
		// A drag out of the palette onto the canvas fires no click on release, so dragging a block in
		// cannot close the flyout out from under the drag; a pointerdown would unmount it mid-drag.
		expect(source).not.toMatch(/onpointerdown=\{[^}]*railFlyout/);
	});
});
