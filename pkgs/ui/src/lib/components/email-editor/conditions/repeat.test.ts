/** REPETITION IS A PROPERTY OF THE ROW — one `<li>` marked as repeating, and the compile step writes
 *  the loop. Hand-typed `{% for %}` tags are a default no author can rebuild, and the canvas then lies. */
import { describe, expect, it } from 'vitest';
import { decodeRepeat, encodeRepeat, encodeVisibility, REPEAT_ATTR } from './attrs';
import { compileConditionals, hasAuthoredConditions } from './compile';

const REP = encodeRepeat({ field: 'item_titles', alias: 'item' });

describe('a repeating row', () => {
	it('compiles to the loop the hand-written default spelled out', () => {
		const source = `<ul><li ${REPEAT_ATTR}="${REP}" style="margin:0 0 8px">{{ item }}</li></ul>`;
		expect(compileConditionals(source)).toBe(
			'<ul>{% for item in item_titles %}<li style="margin:0 0 8px">{{ item }}</li>{% endfor %}</ul>'
		);
	});

	it('strips its own bookkeeping from what ships', () => {
		const compiled = compileConditionals(`<li ${REPEAT_ATTR}="${REP}">{{ item }}</li>`);
		expect(compiled).not.toContain(REPEAT_ATTR);
		expect(compiled).not.toContain('data-');
	});

	it('wraps the loop OUTSIDE the row’s own condition', () => {
		// The order is load-bearing: a condition inside the loop can test the item, one outside it cannot.
		const vis = encodeVisibility({
			action: 'show',
			match: 'all',
			rules: [{ field: 'item', op: 'set' }]
		});
		const compiled = compileConditionals(
			`<li ${REPEAT_ATTR}="${REP}" data-if="${vis}">{{ item }}</li>`
		);
		expect(compiled).toBe(
			'{% for item in item_titles %}{% if item %}<li>{{ item }}</li>{% endif %}{% endfor %}'
		);
	});

	it('is seen by the cheap gate, or the compile never runs', () => {
		// `hasAuthoredConditions` decides whether to compile at all; an unseen repeat ships the attribute.
		expect(hasAuthoredConditions(`<li ${REPEAT_ATTR}="${REP}">x</li>`)).toBe(true);
	});

	it('defaults the alias rather than trusting the attribute', () => {
		expect(decodeRepeat(encodeRepeat({ field: 'item_titles', alias: '' }))).toEqual({
			field: 'item_titles',
			alias: 'item'
		});
	});

	it('reads a field-less repeat as no repeat at all', () => {
		expect(decodeRepeat(encodeRepeat({ field: '   ', alias: 'item' }))).toBeNull();
		expect(decodeRepeat('not-ours')).toBeNull();
		expect(decodeRepeat(null)).toBeNull();
	});
});

describe('a condition with a filter', () => {
	it('tests the FILTERED field, which is what the note guard needs', () => {
		// Guarded on the RENDERED note: whitespace-only renders to nothing, where a bare test is true.
		const vis = encodeVisibility({
			action: 'show',
			match: 'all',
			rules: [{ field: 'message', op: 'set', filter: 'paragraphs' }]
		});
		expect(compileConditionals(`<div data-if="${vis}">x</div>`)).toBe(
			'{% if message | paragraphs %}<div>x</div>{% endif %}'
		);
	});

	it('leaves an unfiltered rule exactly as it was', () => {
		const vis = encodeVisibility({
			action: 'show',
			match: 'all',
			rules: [{ field: 'message', op: 'set' }]
		});
		expect(compileConditionals(`<div data-if="${vis}">x</div>`)).toBe(
			'{% if message %}<div>x</div>{% endif %}'
		);
	});

	it('validates the filter like a field, not as free text', () => {
		const vis = encodeVisibility({
			action: 'show',
			match: 'all',
			rules: [{ field: 'message', op: 'set', filter: "x %} evil {{ '" }]
		});
		const compiled = compileConditionals(`<div data-if="${vis}">x</div>`);
		expect(compiled).not.toContain('evil');
	});
});
