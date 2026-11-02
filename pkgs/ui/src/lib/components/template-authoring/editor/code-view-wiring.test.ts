import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * How the shared `</>` pane is joined to this editor.
 *
 * Beside the editor rather than in the package, for the reason `style-wiring.test.ts` gives: these
 * assertions are about the CALLER's shape, and the package has no canvas of its own.
 */
const HERE = import.meta.dirname;
const editor = readFileSync(join(HERE, '..', 'TemplateEditor.svelte'), 'utf8');
const mount = editor.match(/<CodeView[\s\S]*?\/>/)?.[0] ?? '';

describe('the code pane shows THIS document', () => {
	it('is mounted at all', () => {
		expect(mount).not.toBe('');
	});

	it('offers both shapes, named for a document', () => {
		// "Sent" is the email editor's word and means nothing here; what this compiles to is the
		// Jinja template the renderer is handed.
		expect(mount).toContain("source: 'Editor'");
		expect(mount).toContain("output: 'Template'");
	});

	it('colours the attributes THIS editor writes', () => {
		// Not the email editor's list — see `marker-attrs.ts` and its drift test.
		expect(mount).toContain('markerAttrs={EDITOR_ATTRS}');
	});

	it('withholds the unlock when the editor is read-only, and offers the pane anyway', () => {
		// Reading and copying the markup of a template you may only read is a real thing to want.
		expect(mount).toContain('onapply={readOnly ? undefined : applyHandEditedHtml}');
		expect(editor).toMatch(/aria-label="View HTML"/);
		const button = editor.match(/aria-label="View HTML"[\s\S]{0,400}?<\/Button>/)?.[0] ?? '';
		expect(button).not.toContain('readOnly');
	});

	it('reads the document on demand, following edits made behind it', () => {
		// A cached copy is one that can be stale while looking authoritative. `editorTick` is the
		// dependency that makes the pane follow the canvas.
		expect(editor).toMatch(/const codeHtml = \$derived\.by\(\(\) => \{\s*\n\s*void editorTick;/);
	});

	it('survives a document that cannot be compiled yet', () => {
		// A half-authored condition throws in `buildSchema`. Going blank would read as "this document
		// is empty", which is the wrong answer to "show me the markup".
		expect(editor).toMatch(/catch \{[\s\S]{0,400}?cannot be compiled yet/);
	});

	it('puts a hand edit back through the canvas, and marks the template dirty', () => {
		// `setComponents` reparses, which is what makes the edit real.
		expect(editor).toContain('editor.setComponents?.(html);');
		const apply = editor.match(/function applyHandEditedHtml[\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(apply).toContain('scheduleAutosave()');
		expect(apply).toContain('readOnly');
	});
});

describe('the canvas overlay and the code pane colour DIFFERENT things', () => {
	const script = readFileSync(
		// Served by the APP, not this package: the editor moved to `pkgs/ui` so the admin portal can
		// mount it, but its canvas still loads these scripts by URL from whichever app hosts it.
		join(
			HERE,
			'..',
			'..',
			'..',
			'..',
			'..',
			'..',
			'..',
			'apps',
			'platform',
			'static',
			'grapesjs-jinja-vars.js'
		),
		'utf8'
	);

	/**
	 * A KNOWN DIFFERENCE, pinned so it is a decision rather than a surprise.
	 *
	 * `jinjaRuns` (the code pane) finds both `{{ field }}` and `{% tag %}`. The canvas script tints
	 * only `{{ }}`. That is defensible — control flow here is authored through the panel as `data-*`
	 * attributes, so a `{% %}` on the canvas is hand-typed and rare — but it does mean the same
	 * template shows two different sets of highlights depending on where you look at it.
	 *
	 * The email editor keeps its script and this matcher in lockstep and has a test that extracts
	 * the script and compares them. Doing the same here would FAIL, because the two genuinely differ
	 * — so this records the difference instead. Closing it means teaching the canvas script `{% %}`,
	 * at which point this test is what tells you the parity test is now worth writing.
	 */
	it('the canvas tints merge fields only', () => {
		expect(script).toContain('/\\{\\{[^{}]*\\}\\}/g');
		expect(script).not.toMatch(/\{%/);
	});
});
