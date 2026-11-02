import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { loadInto } from './load';

/** A stand-in editor recording what `loadInto` asked of it. */
function fakeEditor(opts: { projectThrows?: boolean } = {}) {
	const calls: string[] = [];
	return {
		calls,
		loadProjectData: vi.fn(() => {
			calls.push('project');
			if (opts.projectThrows) throw new Error('malformed project');
		}),
		setComponents: vi.fn((html: string) => calls.push(`components:${html}`)),
		setStyle: vi.fn((css: string) => calls.push(`style:${css}`))
	};
}

describe('loadInto', () => {
	it('prefers the GrapesJS project when there is one', () => {
		const ed = fakeEditor();
		loadInto(ed, { gjs_project: { pages: [] }, html: '<p>hi</p>' });
		expect(ed.calls).toEqual(['project']);
	});

	it('falls back to the saved HTML when the project blob is missing', () => {
		// The case that opened a template with content on a BLANK canvas. The saved HTML is not a
		// lesser copy of the project blob — it is what the API previews and what the render worker
		// generates the PDF from.
		const ed = fakeEditor();
		loadInto(ed, { html: '<p>hi</p>', css: 'p{color:red}' });
		expect(ed.setComponents).toHaveBeenCalledWith('<div class="page"><p>hi</p></div>');
		expect(ed.setStyle).toHaveBeenCalledWith('p{color:red}');
	});

	it('falls back to the HTML when the project blob is unreadable', () => {
		const ed = fakeEditor({ projectThrows: true });
		loadInto(ed, { gjs_project: { nonsense: true }, html: '<p>hi</p>' });
		expect(ed.calls).toEqual(['project', 'components:<div class="page"><p>hi</p></div>']);
	});

	it('gives a blank page when there is genuinely nothing', () => {
		const ed = fakeEditor();
		loadInto(ed, null);
		expect(ed.setComponents).toHaveBeenCalledWith('<div class="page"></div>');
	});

	it('keeps HTML that already declares its own pages', () => {
		const ed = fakeEditor();
		loadInto(ed, { html: '<div class="page"><p>hi</p></div>' });
		expect(ed.setComponents).toHaveBeenCalledWith('<div class="page"><p>hi</p></div>');
	});
});

describe('the editor hands loadInto everything it has', () => {
	// A source scan, because the defect was in the CALLER and every test above passed while it was
	// live: `loadInto` has known how to fall back to HTML since it was written, and the editor
	// gated on `gjs_project` alone, so the fallback was unreachable from the one path that matters.
	const editor = readFileSync(join(import.meta.dirname, '..', 'TemplateEditor.svelte'), 'utf8');

	it('passes html and css alongside the project blob', () => {
		expect(editor).toMatch(
			/const initialData = stored[\s\S]{0,160}html: stored\.html[\s\S]{0,40}css: stored\.css/
		);
	});

	it('does not gate the restore on the project blob alone', () => {
		expect(editor).not.toMatch(/stored\?\.gjs_project \? \{ gjs_project: stored\.gjs_project \}/);
	});

	it('stores an explicit null rather than dropping the key', () => {
		// `JSON.stringify` omits a key whose value is `undefined`, so a saved version would carry no
		// `gjs_project` at all and a reader could not tell "no project" from "project failed".
		expect(editor).toMatch(/gjs_project: editor\.getProjectData\?\.\(\) \?\? null/);
	});
});

describe('a failed editor start is visible', () => {
	// The screen this produced was indistinguishable from an empty template: GrapesJS never came
	// up, so `editor` stayed null — and every derived readout then reported its own DEFAULT rather
	// than a fault. `pageCount` is `$state(1)`, so the rail said "Pages (1)" for a page that did not
	// exist; `canvasEmpty` returns false with no editor, so neither the empty-page hint nor the
	// auto-opened palette appeared. An editor that looks loaded and draws nothing sends you looking
	// for a data problem that is not there.
	const editor = readFileSync(join(import.meta.dirname, '..', 'TemplateEditor.svelte'), 'utf8');

	it('catches a rejected init instead of leaving an unhandled promise', () => {
		// The IIFE is never awaited, so a throw inside it reaches nothing.
		expect(editor).toMatch(/\}\)\(\)\.catch\(\(err\) => \{/);
		expect(editor).toMatch(/console\.error\('\[TemplateEditor\] editor failed to initialise'/);
	});

	it('reports the missing-container bail rather than returning silently', () => {
		expect(editor).toMatch(
			/if \(!canvasEl \|\| !blocksEl \|\| !layersEl \|\| !traitsEl \|\| !stylesEl\)[\s\S]{0,600}?initError =/
		);
	});

	it('renders the failure over the canvas, where the reader is already looking', () => {
		expect(editor).toMatch(/\{#if initError\}/);
		// Not a toast: the failure IS the blank canvas, so it must not be dismissable back into the
		// same silence.
		expect(editor).not.toMatch(/toast\.\w+\(initError/);
	});
});

describe('GrapesJS CJS/ESM interop', () => {
	// The real defect behind the blank canvas: `grapesjs-blocks-basic` is a UMD bundle whose
	// `module.exports` is `{ __esModule: true, default: fn }`, so `import()` can hand back the
	// WRAPPER. `grapesjs.init` then called the wrapper as a plugin and the whole editor died with
	// "blocksBasic is not a function" — which the reader sees as an empty canvas, not a bad import.
	const create = readFileSync(join(import.meta.dirname, 'create.ts'), 'utf8');

	it('resolves the two packages by their OWN shapes, not one rule for both', () => {
		// They want opposite things: grapesjs is an OBJECT (identified by `.init`), the plugin is a
		// FUNCTION. A single "unwrap until callable" helper walks past the grapesjs namespace and
		// throws "grapesjs did not export a function" — swapping one broken editor for another,
		// which is exactly what happened while fixing this.
		expect(create).toMatch(/function resolveGrapesJs\(/);
		expect(create).toMatch(/typeof m\?\.default\?\.init === 'function' \? m\.default : m/);
		expect(create).toMatch(/function resolveGrapesPlugin\(/);
		expect(create).toMatch(/\[m\?\.default, m\?\.default\?\.default, m\]\.find/);
	});

	it('never destructures `default` off either import', () => {
		// A bare `{ default: … }` destructure is a bet on one interop shape, and the shape is not
		// stable between the dev server, a build, and a dependency-optimiser rebuild.
		expect(create).not.toMatch(/const \[\{ default: grapesjs \}/);
		expect(create).toMatch(/const grapesjs = resolveGrapesJs\(grapesjsMod\)/);
		expect(create).toMatch(/const blocksBasic = resolveGrapesPlugin\(blocksBasicMod,/);
	});

	it('names the package when a plugin cannot be resolved', () => {
		expect(create).toMatch(/did not resolve to a plugin function/);
	});
});

// The resolvers themselves, against every shape the interop actually produces. Reimplemented here
// because `create.ts` dynamic-imports GrapesJS at module scope and cannot be loaded in node — the
// source scan above is what keeps these two in step.
const resolveGrapesJs = (mod: unknown): unknown => {
	const m = mod as { default?: { init?: unknown } };
	return typeof m?.default?.init === 'function' ? m.default : m;
};
const resolveGrapesPlugin = (mod: unknown, name: string): unknown => {
	const m = mod as { default?: { default?: unknown } };
	const fn = [m?.default, m?.default?.default, m].find((c) => typeof c === 'function');
	if (typeof fn !== 'function') throw new Error(`${name} did not resolve to a plugin function`);
	return fn;
};

describe('resolveGrapesJs', () => {
	const ns = { init: () => 'editor' };

	it('takes the namespace out of a default wrapper', () => {
		expect(resolveGrapesJs({ default: ns })).toBe(ns);
	});

	it('takes the module itself when it is already the namespace', () => {
		expect(resolveGrapesJs(ns)).toBe(ns);
	});

	it('identifies it by `.init`, not by position', () => {
		// A `default` that is some other object is not the namespace.
		const both = { init: () => 'outer', default: { notInit: true } };
		expect(resolveGrapesJs(both)).toBe(both);
	});
});

describe('resolveGrapesPlugin', () => {
	const plugin = () => 'installed';

	it('accepts a plain ESM default', () => {
		expect(resolveGrapesPlugin({ default: plugin }, 'p')).toBe(plugin);
	});

	it('accepts the DOUBLE wrapper a UMD bundle produces', () => {
		// `{ __esModule: true, default: fn }` reached through `import()` — the shape that broke it.
		expect(resolveGrapesPlugin({ default: { __esModule: true, default: plugin } }, 'p')).toBe(
			plugin
		);
	});

	it('accepts a module that is itself the function', () => {
		expect(resolveGrapesPlugin(plugin, 'p')).toBe(plugin);
	});

	it('throws WITH the package name when nothing is callable', () => {
		expect(() => resolveGrapesPlugin({ default: { nope: 1 } }, 'grapesjs-blocks-basic')).toThrow(
			'grapesjs-blocks-basic did not resolve to a plugin function'
		);
	});
});
