import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/** The email's HEAD — subject and inbox preview — asserted as SOURCE SHAPES, since nothing here lays out a
 *  GrapesJS canvas. Three failures found by eye: no SCROLL (the canvas COLUMN is the scroller), it VANISHED
 *  inside `.gjs-cv-canvas` (an absolute frames layer paints over an in-flow sibling), and it sat LEFT because
 *  centring lived in the injected sheet, which its element id keeps from being replaced. From/Reply-To are gone. */
const HERE = import.meta.dirname;
const create = readFileSync(join(HERE, 'create.ts'), 'utf8');
const editor = readFileSync(join(HERE, 'email-editor.svelte'), 'utf8');
const page = readFileSync(join(HERE, 'email-template-editor-page.svelte'), 'utf8');

const sync = create.match(/export function syncHeadWidth[\s\S]*?\n}/)?.[0] ?? '';

describe('the head scrolls with the email, from outside GrapesJS', () => {
	it('has a width sync at all', () => {
		expect(sync, 'syncHeadWidth was not found').not.toBe('');
	});

	it('is NOT moved into GrapesJS’s subtree', () => {
		// Inside `.gjs-cv-canvas` all is absolute, so an in-flow sibling is painted over, and `CanvasView` rewrites it.
		expect(sync).not.toMatch(/insertBefore|appendChild|append\(/);
		expect(create).not.toMatch(/mountDocumentHead/);
	});

	it('makes the COLUMN the scroller, not the canvas', () => {
		// The head must share the card's scrolling box; `listenToEl` defaults to the parentNode and re-offsets.
		const canvasRule = create.match(/'\.gjs-cv-canvas\{[^']*'/)?.[0] ?? '';
		expect(canvasRule, 'the canvas rule was not found').not.toBe('');
		expect(canvasRule).toContain('overflow:visible');
		expect(editor).toMatch(/\.canvas-column \{[^}]*overflow-y: auto/);
	});

	it('sizes the MOUNT to the email, or the column has nothing to scroll', () => {
		// `.gjs-editor` and `.gjs-cv-canvas` are both `height:100%`, so without this a long email is cut off at the fold.
		const frameSync = create.match(/export function syncFrameHeight[\s\S]*?\n}/)?.[0] ?? '';
		expect(frameSync).toMatch(/getContainer\?\.\(\)\?\.style\?\.setProperty\('height'/);
	});

	it('MEASURES the frame’s width rather than restating the device sizes', () => {
		// The frame is the device's width (640/375), read off the wrapper so a third device needs no list kept in step.
		expect(sync).toContain('.gjs-frame-wrapper');
		expect(sync).toMatch(/offsetWidth/);
		expect(sync).toMatch(/head\.style\.width = /);
		expect(sync).not.toMatch(/\b(640|375|600)\b/);
	});

	it('treats a zero measurement as “not known yet”, not as a width', () => {
		expect(sync).toMatch(/w <= 0/);
	});

	it('follows a device change', () => {
		expect(sync).toMatch(/change:device/);
		// The cached width has to be invalidated, or the new device's width is short-circuited away.
		expect(sync).toMatch(/lastWidth = -1/);
	});

	it('disconnects what it observes, since GrapesJS owns that node', () => {
		expect(sync).toMatch(/observer\?\.disconnect\(\)/);
		expect(sync).toMatch(/return \(\) =>/);
	});
});

describe('the head sits on the card’s centre line', () => {
	it('is centred by the COMPONENT’s stylesheet, not the injected one', () => {
		// THE BUG THIS EXISTS FOR: the injected sheet is guarded by its element id, so a hot reload keeps the OLD copy.
		expect(editor).toMatch(/\.canvas-column \{[\s\S]*?align-items: center/);
		const head = create.match(/'\.gjs-email-head\{[^']*'/)?.[0] ?? '';
		expect(head, 'the head sheet rule was not found').not.toBe('');
		expect(head).not.toMatch(/margin/);
	});

	it('stops both children shrinking, or the column has nothing to scroll', () => {
		// THE SECOND SCROLL BUG: a flex item shrinks by default, so both were squashed and there was nothing to scroll.
		expect(editor).toMatch(/\.gjs-email-head \{[\s\S]*?flex: none/);
		expect(editor).toMatch(/\.canvas \{[\s\S]*?flex: none/);
	});

	it('pins the mount to the device width, so the card stays on that line', () => {
		// A MIN/MAX PAIR, not `width`: GrapesJS writes an inline `width: auto` here, which beats any stylesheet rule, so
		// a bare `width` is inert while looking right. Pinned to `--device-w`, the head's own number, not to the parent.
		expect(editor).toMatch(/\.canvas \{[\s\S]*?min-width: var\(--device-w/);
		expect(editor).toMatch(/\.canvas \{[\s\S]*?max-width: var\(--device-w/);
		// Auto margins, not `align-items: center`: they go to zero on negative free space, so the mount clamps left too.
		expect(editor).toMatch(/\.canvas \{[\s\S]*?margin-inline: auto/);
		expect(editor).not.toMatch(/\.canvas \{[\s\S]*?align-self: stretch/);
	});

	it('gives the head the card’s own sheet treatment, declared in one place', () => {
		const head = create.match(/'\.gjs-email-head\{[^']*'/)?.[0] ?? '';
		const frame = create.match(/`\.gjs-frame-wrapper\{[^`]*`/)?.[0] ?? '';
		expect(frame, 'the frame wrapper rule was not found').not.toBe('');
		for (const declaration of ['border-radius:8px', 'box-shadow:0 6px 24px rgba(15,23,42,.12)']) {
			expect(head, `head is missing ${declaration}`).toContain(declaration);
			expect(frame, `frame is missing ${declaration}`).toContain(declaration);
		}
	});
});

describe('one backdrop, declared once', () => {
	it('paints it on the column and nowhere else', () => {
		// The column owns the backdrop because the head and the card must sit on the SAME one, or they read as two rooms.
		expect(editor).toMatch(/\.canvas-column \{[\s\S]*?background: var\(--muted\)/);
		const canvasRule = create.match(/'\.gjs-cv-canvas\{[^']*'/)?.[0] ?? '';
		expect(canvasRule).toContain('background:transparent');
	});

	it('strips the framework’s own dark chrome from the wrapper', () => {
		// `EditorView.render` puts `gjs-one-bg` — a near-black — on `.gjs-editor` unconditionally (grapes.mjs :69646).
		const editorRule = create.match(/'\.gjs-editor\{[^']*'/)?.[0] ?? '';
		expect(editorRule, 'the .gjs-editor rule was not found').not.toBe('');
		expect(editorRule).toContain('background:transparent');
	});
});

describe('the head is the editor’s, and the rows are the page’s', () => {
	it('renders outside the GrapesJS mount point', () => {
		// `canvasEl` is what GrapesJS renders into; a child of it is at the mercy of that render.
		expect(editor).toMatch(/bind:this=\{headEl\}[^>]*class="gjs-email-head"/);
		expect(editor).toMatch(/if \(headEl\) stopHeadMount = syncHeadWidth\(ed, headEl\)/);
	});

	it('carries no sheet styling of its own, so there is one declaration of a sheet', () => {
		// The PAGE's snippet must be rows only: a `rounded-lg border bg-background` here is a second answer to "a sheet".
		const snippet = page.match(/\{#snippet documentHeader\(\)\}([\s\S]*?)\{\/snippet\}/)?.[1] ?? '';
		expect(snippet, 'the documentHeader snippet was not found').not.toBe('');
		expect(snippet).not.toMatch(/rounded-lg|shadow-|bg-background/);
	});

	it('carries no read-only rows', () => {
		// Server-resolved, so a row for either is head spent on nothing actionable. Comments stripped: the note names them.
		const raw = page.match(/\{#snippet documentHeader\(\)\}([\s\S]*?)\{\/snippet\}/)?.[1] ?? '';
		const snippet = raw.replace(/<!--[\s\S]*?-->/g, '');
		expect(snippet).not.toContain('Reply-To');
		expect(snippet).not.toMatch(/>\s*From\s*</);
	});

	it('keeps the save contract: the two editable rows still post to the save form', () => {
		// They sit outside `<form id="et-save">` now, so `form=` is the only thing keeping `?/save` fed at all.
		const snippet = page.match(/\{#snippet documentHeader\(\)\}([\s\S]*?)\{\/snippet\}/)?.[1] ?? '';
		for (const name of ['subject', 'preheader']) {
			const input = snippet.match(new RegExp(`name="${name}"[^>]*`))?.[0] ?? '';
			expect(input, `no ${name} input in the head`).not.toBe('');
			expect(input, `${name} is not bound to the save form`).toContain('form="et-save"');
		}
	});
});
