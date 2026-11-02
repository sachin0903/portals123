import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { encodeVisibility } from './attrs';
import { compileConditionals } from './compile';
import type { Visibility } from './model';

/** THE ROUND TRIP, which fails silently and permanently. `source` is the editor's copy carrying
 *  `data-if`; `html` is render-ready, compiled and stripped. The send path reads `html`, but THE CANVAS
 *  MUST BE SEEDED FROM `source`, or the next save writes the loose `{% if %}` back as content. */
const HERE = import.meta.dirname;
const page = readFileSync(join(HERE, '..', 'email-template-editor-page.svelte'), 'utf8');
const editor = readFileSync(join(HERE, '..', 'email-editor.svelte'), 'utf8');

describe('the two shapes are genuinely different', () => {
	it('compiling is not the identity when a condition is authored', () => {
		const visibility: Visibility = {
			action: 'show',
			match: 'all',
			rules: [{ field: 'is_reminder', op: 'set' }]
		};
		const source = `<td data-if="${encodeVisibility(visibility)}">Due soon.</td>`;
		const html = compileConditionals(source);
		expect(html).not.toBe(source);
		expect(html).toBe('{% if is_reminder %}<td>Due soon.</td>{% endif %}');
		expect(html).not.toContain('data-if');
	});
});

describe('the editor produces both', () => {
	it('reads source from the canvas and derives html from it', () => {
		const read = editor.match(/function read\(\)[\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(read, 'read() was not found').not.toBe('');
		expect(read).toMatch(/const source = editor\?\.getHtml\?\.\(\) \?\? ''/);
		expect(read).toMatch(/compileConditionals\(source\)/);
	});

	it('skips the compile for a template with no conditions', () => {
		// Skipping means the two values are the identical string, which keeps the dirty comparison honest.
		expect(editor).toMatch(
			/hasAuthoredConditions\(source\) \? compileConditionals\(source\) : source/
		);
	});

	it('compares SOURCE for the dirty check', () => {
		// Source is what the canvas produced, so it is what moves when the author does something.
		const emit = editor.match(/function emitNow\(\)[\s\S]*?\n\t\}/)?.[0] ?? '';
		expect(emit).toMatch(/next\.source === lastEmitted\.source/);
	});
});

describe('the page puts each value where it belongs', () => {
	it('SEEDS THE CANVAS FROM SOURCE, never from html', () => {
		// `html={source}` is the line that stops a reopened template losing its conditions.
		expect(page).toMatch(/html=\{source\}/);
		expect(page).not.toMatch(/<EmailEditor[\s\S]*?\n\t\t\t\{html\}/);
	});

	it('falls back to html for a version saved before source existed', () => {
		expect(page).toMatch(/source = v\?\.schema\?\.source \?\? v\?\.schema\?\.html \?\? ''/);
	});

	it('posts all three, and reads them live at submit', () => {
		expect(page).toMatch(/name="source" value=\{source\}/);
		expect(page).toMatch(/formData\.set\('source', c\.source\)/);
	});

	it('keeps all three in step as the canvas changes', () => {
		const onChange = page.match(/onChange=\{\(v\) => \{[\s\S]*?\}\}/)?.[0] ?? '';
		expect(onChange).toContain('html = v.html');
		expect(onChange).toContain('source = v.source');
		expect(onChange).toContain('css = v.css');
	});
});
