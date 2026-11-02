import { globSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EDITOR_ATTRS } from './marker-attrs';

/**
 * `EDITOR_ATTRS` against the editor's own source, in BOTH directions.
 *
 * The list exists to colour the editor's bookkeeping in the code view, so an author hand-editing
 * the markup can see what not to drop. A stale list fails silently and in the worse direction: a
 * new attribute is simply not coloured, reads as ordinary markup, and gets deleted.
 *
 * Scanned rather than maintained by hand for the reason the list itself gives — the alternative is
 * remembering, and nothing here would catch forgetting.
 */
const HERE = import.meta.dirname;
const ROOT = join(HERE, '..');

/** Every `data-*` literal the editor writes, from its own source. */
function attrsInSource(): Set<string> {
	const found = new Set<string>();
	for (const rel of globSync('**/*.{ts,svelte}', { cwd: ROOT })) {
		// The list and its own test are not sources of truth about what the editor writes.
		if (rel.endsWith('marker-attrs.ts') || rel.endsWith('marker-attrs.test.ts')) continue;
		const source = readFileSync(join(ROOT, rel), 'utf8');
		for (const [, name] of source.matchAll(/'(data-[a-z][a-z-]*)'/g)) if (name) found.add(name);
	}
	return found;
}

describe('the code view’s marker list', () => {
	const inSource = attrsInSource();

	it('finds the editor’s source, so the assertions below cannot pass vacuously', () => {
		expect(inSource.size).toBeGreaterThan(20);
	});

	it('covers every data-* attribute the editor writes', () => {
		const missing = [...inSource].filter((a) => !EDITOR_ATTRS.includes(a)).sort();
		expect(missing, 'add these to EDITOR_ATTRS, or stop writing them').toEqual([]);
	});

	it('lists none the editor no longer writes', () => {
		// The other direction matters too: a retired attribute left here colours nothing, but it is a
		// claim about the markup that stopped being true, and the next reader believes it.
		const stale = EDITOR_ATTRS.filter((a) => !inSource.has(a)).sort();
		expect(stale, 'remove these from EDITOR_ATTRS').toEqual([]);
	});

	it('is lower-case and unique', () => {
		// `highlight.ts` lower-cases on the way in; a duplicate or an upper-cased entry would be a
		// silent no-op rather than an error.
		expect(EDITOR_ATTRS).toEqual([...new Set(EDITOR_ATTRS)]);
		expect(EDITOR_ATTRS.filter((a) => a !== a.toLowerCase())).toEqual([]);
	});
});
