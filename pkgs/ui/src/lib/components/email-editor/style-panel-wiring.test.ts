import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/** How the SHARED style rail is JOINED to GrapesJS by THIS editor: wiring that fails silently when
    dropped. It sits beside the editor rather than beside the panel because the panel is shared —
    what it asserts is this editor's setup (`create.ts`), not the rail's own behaviour. */
const HERE = import.meta.dirname;
const editor = readFileSync(join(HERE, 'email-editor.svelte'), 'utf8');
const createSource = readFileSync(join(HERE, 'create.ts'), 'utf8');

/** Comments stripped first: both files NAME the retired ids in prose, and this scans for CODE. */
function stripComments(code: string): string {
	return code
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/\/\*[\s\S]*?\*\//g, '')
		.replace(/(^|[^:'"`\w])\/\/[^\n]*/g, '$1');
}
const create = stripComments(createSource);

describe('GrapesJS renders neither panel itself', () => {
	it('marks the STYLE and SELECTOR managers custom', () => {
		// `custom: true` suppresses the default UI only; drop it and GrapesJS's dark HTML renders too.
		expect(create).toMatch(/styleManager: \{ custom: true \}/);
		expect(create).toMatch(/selectorManager: \{ custom: true \}/);
	});

	it('does NOT mark the block manager custom', () => {
		// The exception: `custom: true` here renders no block element, so the drag must be hand-driven, and
		// `BlockManager.startDrag` goes through `Droppable`, whose sorter ignores the drop correction.
		expect(create).not.toMatch(/blockManager: \{ custom: true \}/);
		expect(create).toMatch(/blockManager: \{[\s\S]*?appendTo: opts\.blocksEl,/);
		expect(create).toMatch(/appendOnClick: \(block: any\) => appendBlockOnClick\(editor, block\)/);
		expect(create).not.toMatch(/nativeDnD/);
	});

	it('mounts nothing into the retired STYLE panel containers', () => {
		// An `appendTo` at a missing element renders into `document.body`, outside every scroll container.
		for (const id of ['gjs-styles-panel', 'gjs-traits-panel', 'gjs-layers-panel']) {
			expect(create, `${id} is still referenced`).not.toContain(id);
			expect(stripComments(editor), `${id} is still referenced`).not.toContain(id);
		}
	});

	it('drops Spectrum’s configuration with Spectrum', () => {
		// The colour picker is ours, so `colorPicker` would configure a component nothing renders.
		expect(create).not.toMatch(/colorPicker/);
	});
});

describe('the panel re-reads at the right moments', () => {
	const bumped =
		editor.match(/for \(const ev of \[([\s\S]*?)\]\)\s*\n\s*ed\.on\(ev, bump\);/)?.[1] ?? '';

	it('finds the bump loop, so the assertions below cannot pass vacuously', () => {
		expect(bumped).not.toBe('');
	});

	it('bumps on the StyleManager’s own target events', () => {
		// GrapesJS resolves the style target on a DEBOUNCED handler (grapes.mjs :67165), so on
		// `component:selected` the models still answer for the PREVIOUS selection — and an edit writes it.
		expect(bumped).toContain("'style:target'");
		expect(bumped).toContain("'style:custom'");
	});

	it('bumps on the other four directions a value can move from', () => {
		for (const ev of ['undo', 'redo', 'component:styleUpdate', 'rte:disable'])
			expect(bumped, `no re-read on '${ev}'`).toContain(`'${ev}'`);
	});

	it('renders the panel only for a selection, and the PAGE panel otherwise', () => {
		// With no target every control shows its DEFAULT: numbers belonging to nothing, writes nowhere.
		expect(editor).toMatch(/\{#if selectedName\}[\s\S]*?<StylePanel/);
		// The idle branch is the page's background and inset, unreachable by the rail (`protectPage`).
		expect(editor).toMatch(/\{:else if !readOnly\}[\s\S]*?<PageStyle/);
		expect(editor).not.toContain('rail-hint');
	});

	it('emits content upward after a panel write, not on the debounce', () => {
		// An emit sets `dirty`, so an edit surfacing 250ms later leaves Save looking unnecessary.
		const handler = editor.match(/<StylePanel[\s\S]*?\/>/)?.[0] ?? '';
		expect(handler).toContain('emitNow()');
	});
});
