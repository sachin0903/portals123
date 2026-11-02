import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * How the shared style rail is JOINED to this editor.
 *
 * All of this fails SILENTLY when dropped: the panel renders, it just shows the wrong element's
 * values, or GrapesJS's own dark rail reappears underneath ours, or an edit never reaches autosave.
 * None of it is a type error and none of it makes the editor look broken, which is exactly why it is
 * scanned. The equivalent test lives beside every editor that mounts `@graphene/ui/grapes` — it
 * asserts on the CALLER's shape, so it cannot live in the package.
 */
const HERE = import.meta.dirname;
const create = readFileSync(join(HERE, 'create.ts'), 'utf8');
const editor = readFileSync(join(HERE, '..', 'TemplateEditor.svelte'), 'utf8');

/** Comments stripped first: both files NAME these ids in prose, and this scans for CODE. */
function stripComments(code: string): string {
	return code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(^|[^:'"`\w])\/\/[^\n]*/g, '$1');
}

describe('GrapesJS does not render the style rail itself', () => {
	it('marks the style manager custom, and keeps its sectors', () => {
		// Drop `custom` and GrapesJS's own UI renders too, with its own stylesheet fighting the
		// tenant's theme. Drop the sectors and the panel has nothing to show.
		expect(stripComments(create)).toMatch(
			/styleManager: \{ custom: true, sectors: STYLE_SECTORS \}/
		);
	});

	it('no longer mounts the style manager into the rail element', () => {
		// An `appendTo` here would put GrapesJS's rail directly above ours, both live, both writing.
		expect(stripComments(create)).not.toMatch(/styleManager: \{[^}]*appendTo/);
	});

	it('keeps the SELECTOR manager mounted — it is a different surface', () => {
		// The class/state control is not the property rail, and blanking it is a separate decision.
		expect(stripComments(create)).toMatch(/selectorManager: \{[^}]*appendTo: opts\.stylesEl/);
	});
});

describe('the panel re-reads at the right moments', () => {
	const bumped =
		editor.match(
			/for \(const ev of \[([\s\S]*?)\]\)\s*\n\s*ed\.on\(ev, \(\) => styleRevision\+\+\);/
		)?.[1] ?? '';

	it('finds the bump loop, so the assertions below cannot pass vacuously', () => {
		expect(bumped).not.toBe('');
	});

	it('bumps on the StyleManager’s own target events', () => {
		// GrapesJS resolves the style target on a DEBOUNCED handler, so on `component:selected` the
		// models still answer for the PREVIOUS selection — and a write then lands on that element.
		expect(bumped).toContain("'style:target'");
		expect(bumped).toContain("'style:custom'");
	});

	it('bumps on the other four directions a value can move from', () => {
		for (const ev of ['undo', 'redo', 'component:styleUpdate', 'rte:disable'])
			expect(bumped, `no re-read on '${ev}'`).toContain(`'${ev}'`);
	});
});

describe('the panel is mounted correctly', () => {
	const mount = editor.match(/<StylePanel[\s\S]*?\/>/)?.[0] ?? '';

	it('is rendered only for a selection', () => {
		// With no target every control shows its DEFAULT: numbers belonging to nothing, writes nowhere.
		expect(editor).toMatch(
			/\{#if selectedComp && !cssMode && !hideStyle\}[\s\S]{0,200}?<StylePanel/
		);
	});

	it('is given the live StyleManager and the revision', () => {
		expect(mount).toContain('styleManager={editor?.StyleManager}');
		expect(mount).toContain('revision={styleRevision}');
	});

	it('reaches autosave, and re-reads what the write produced', () => {
		// A composite can normalise a value, so the control must show the STORED one rather than the
		// typed one — and a style edit is an edit, so it must mark the template dirty like any other.
		expect(mount).toContain('styleRevision++');
		expect(mount).toContain('scheduleAutosave()');
	});

	it('is read-only when the editor is', () => {
		expect(mount).toContain('disabled={readOnly}');
	});
});

describe('the right rail is one accordion', () => {
	// Six sections had hand-rolled headers — a `<button>`, an inline-SVG chevron rotated by a class,
	// and their own `*Open` state — and they had already drifted: two used `flex-1` inside a wrapper
	// and one used `w-full`, one wrote `text-foreground/70` first and another last, and none of them
	// got `Collapsible`'s keyboard handling. This is the same drift `InspectorSector` was written for
	// in the email editor, one level up.
	it('has no hand-rolled section header left', () => {
		// The exact class string those six shared. A new section copying one of them fails here.
		expect(editor).not.toContain('text-[11px] font-semibold uppercase tracking-[0.05em]');
	});

	it('heads every band with InspectorSector', () => {
		// Six sections plus the style panel's own sectors, which render InspectorSector internally.
		const bands = editor.match(/<InspectorSector/g) ?? [];
		expect(bands.length).toBeGreaterThanOrEqual(6);
	});

	it('has a rail head that names the target and does not scroll away', () => {
		// The target's name used to be the first row of the SCROLL area, so it left the screen exactly
		// when you reached the properties it names — and a rail that will not say what it is editing is
		// how you restyle the wrong element. `flex-none` is what keeps it put.
		expect(editor).toMatch(/flex min-h-9 flex-none items-center[^>]*>[\s\S]{0,600}?\{railTitle\}/);
	});

	it('does not name the target twice', () => {
		// The head and the old target indicator both rendered the name; the indicator keeps only what
		// the head cannot say — the tag and classes a style rule will actually match.
		expect(editor).not.toContain("{targetName || (jinjaVarKey !== null ? 'Variable' : 'Element')}");
	});
});
