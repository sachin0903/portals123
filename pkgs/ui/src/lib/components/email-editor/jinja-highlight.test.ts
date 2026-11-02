/** The email canvas's Jinja matcher, plus the guarantee that the canvas uses THIS one: the static asset inside
 *  the GrapesJS iframe cannot be imported, so it is extracted between its own markers and run through one table. */
import { describe, expect, it } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { jinjaRuns, type JinjaRun } from './jinja-highlight';

// fileURLToPath, not `.pathname`: on Windows the latter yields `/C:/…`, making the whole suite vacuous.
const REPO_ROOT = fileURLToPath(new URL('../../../../../../', import.meta.url));
const SCRIPT = join(REPO_ROOT, 'apps/platform/static/gjs-email-jinja-hl.js');

const runsOf = (text: string): string[] =>
	jinjaRuns(text).map((r) => `${r.kind}:${text.slice(r.start, r.end)}`);

describe('jinjaRuns', () => {
	it('tells control flow from a merge field', () => {
		expect(runsOf('{% if brand.support_email %}')).toEqual(['tag:{% if brand.support_email %}']);
		expect(runsOf('{{ client.first_name }}')).toEqual(['field:{{ client.first_name }}']);
	});

	it('classifies every control keyword the same way — it is the delimiter that decides, not the word', () => {
		for (const tag of [
			'{% if x %}',
			'{% else %}',
			'{% endif %}',
			'{% for a in b %}',
			'{% endfor %}'
		])
			expect(jinjaRuns(tag)[0]?.kind).toBe('tag');
	});

	it('finds several runs in one string, in document order', () => {
		expect(
			runsOf(
				'Hello {{ client.first_name }}, {% if account %}you hold {{ account.name }}{% endif %}'
			)
		).toEqual([
			'field:{{ client.first_name }}',
			'tag:{% if account %}',
			'field:{{ account.name }}',
			'tag:{% endif %}'
		]);
	});

	it('returns offsets that slice back to the matched run', () => {
		const text = 'a {{ b }} c {% if d %} e';
		for (const run of jinjaRuns(text)) {
			const slice = text.slice(run.start, run.end);
			expect(slice.startsWith('{')).toBe(true);
			expect(slice.endsWith('}')).toBe(true);
		}
	});

	it('does not match an unclosed opener — a half-typed tag must not swallow the paragraph', () => {
		expect(jinjaRuns('{% if brand.support_email')).toEqual([]);
		expect(jinjaRuns('{{ client.first_name')).toEqual([]);
		expect(jinjaRuns('{%')).toEqual([]);
		expect(jinjaRuns('{{')).toEqual([]);
		expect(jinjaRuns('{{ x %}')).toEqual([]);
		expect(jinjaRuns('{% x }}')).toEqual([]);
	});

	it('keeps decorating a good run that follows an unclosed one', () => {
		expect(runsOf('{% if x {{ client.first_name }}')).toEqual(['field:{{ client.first_name }}']);
	});

	it('ends a run at the FIRST closer, so an inner opener is swallowed rather than starting a second', () => {
		expect(runsOf('{{ {{ a }} }}')).toEqual(['field:{{ {{ a }}']);
		expect(runsOf('{% if a {% if b %}')).toEqual(['tag:{% if a {% if b %}']);
		expect(runsOf('{{ a }}}')).toEqual(['field:{{ a }}']);
	});

	it('does not double-match a {{ inside a {% %}', () => {
		// One run, kind 'tag' — the field-looking text inside the condition is part of the tag.
		expect(runsOf('{% if x == "{{" %}')).toEqual(['tag:{% if x == "{{" %}']);
		expect(runsOf('{% if a %}{{ b }}{% endif %}')).toEqual([
			'tag:{% if a %}',
			'field:{{ b }}',
			'tag:{% endif %}'
		]);
	});

	it('matches whitespace-control delimiters, which authors do use', () => {
		expect(runsOf('{%- if a -%}')).toEqual(['tag:{%- if a -%}']);
		expect(runsOf('{{- a -}}')).toEqual(['field:{{- a -}}']);
	});

	it('ignores text with no Jinja in it at all, including a Jinja comment', () => {
		expect(jinjaRuns('')).toEqual([]);
		expect(jinjaRuns('Dear client, thank you for your instruction.')).toEqual([]);
		// `{# … #}` is deliberately not a run: a comment never reaches a recipient, so it misleads nobody.
		expect(jinjaRuns('{# internal note #}')).toEqual([]);
	});

	it('matches an empty run rather than choking on it', () => {
		expect(runsOf('{{}}')).toEqual(['field:{{}}']);
		expect(runsOf('{%%}')).toEqual(['tag:{%%}']);
	});
});

const AGREEMENT_CASES = [
	'',
	'Dear client, thank you for your instruction.',
	'{# internal note #}',
	'{% if brand.support_email %}',
	'{{ client.first_name }}',
	'Hello {{ client.first_name }}, {% if account %}you hold {{ account.name }}{% endif %}',
	'{% if brand.support_email',
	'{{ client.first_name',
	'{%',
	'{{',
	'{{ x %}',
	'{% x }}',
	'{% if x {{ client.first_name }}',
	'{{ {{ a }} }}',
	'{% if a {% if b %}',
	'{{ a }}}',
	'{% if x == "{{" %}',
	'{% if a %}{{ b }}{% endif %}',
	'{%- if a -%}',
	'{{- a -}}',
	'{{}}',
	'{%%}',
	'{% for holding in holdings %}{{ holding.name }}: {{ holding.value | money }}{% endfor %}'
];

describe('the canvas script’s own matcher', () => {
	const source = readFileSync(SCRIPT, 'utf8');

	/** The function as the canvas runs it, lifted out between the markers the script carries for this. */
	const extracted = (): string => {
		const begin = source.indexOf('jinjaRuns: BEGIN');
		const end = source.indexOf('jinjaRuns: END');
		expect(
			begin,
			'the BEGIN marker is gone — this suite would otherwise pass vacuously'
		).toBeGreaterThan(-1);
		expect(
			end,
			'the END marker is gone — this suite would otherwise pass vacuously'
		).toBeGreaterThan(begin);
		return source.slice(source.indexOf('\n', begin) + 1, source.lastIndexOf('\n', end));
	};

	it('is extractable and still declares jinjaRuns', () => {
		expect(extracted()).toContain('function jinjaRuns(');
	});

	it('agrees with this module on every case, so the canvas cannot drift from the tested rules', () => {
		const canvasJinjaRuns = new Function(`${extracted()}\nreturn jinjaRuns;`)() as (
			text: string
		) => JinjaRun[];
		for (const text of AGREEMENT_CASES)
			expect(canvasJinjaRuns(text), `disagreed on ${JSON.stringify(text)}`).toEqual(
				jinjaRuns(text)
			);
	});
});

/** The file minus its prose: both scans must read CODE, since the script documents the primitives it refuses. */
function stripComments(source: string): string {
	return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

/** Two features, the overlay then the `{{` picker, with a different rule each — split on the file's own marker. */
function splitAtPicker(source: string): [string, string] {
	const marker = source.indexOf('THE `{{` PICKER');
	// Not found ⇒ the whole file is overlay, the stricter reading: a renamed section must not leave it unchecked.
	return marker === -1 ? [source, ''] : [source.slice(0, marker), source.slice(marker)];
}

describe('the canvas script’s overlay rule', () => {
	const source = readFileSync(SCRIPT, 'utf8');

	it('has a script to check', () => {
		expect(existsSync(SCRIPT)).toBe(true);
	});

	it('names none of the APIs that would put a decoration inside the saved email', () => {
		// `getHtml()` is emailed, so a decoration that wrapped or rewrote a content node would ship; its own layer is exempt.
		const forbidden = [
			'surroundContents(',
			'insertNode(',
			'insertAdjacentHTML(',
			'.innerHTML',
			'.outerHTML',
			'execCommand(',
			'splitText(',
			'deleteContents(',
			'extractContents(',
			'replaceChild('
		];
		// SCOPED TO THE OVERLAY HALF: the picker legitimately WRITES, since `execCommand` is what leaves the RTE's undo
		// stack recognisable. SPLIT FIRST, THEN STRIP — the marker is a comment, so stripping first hides the boundary.
		const [rawOverlay = '', rawPicker = ''] = splitAtPicker(source);
		const overlay = stripComments(rawOverlay);
		const picker = stripComments(rawPicker);
		expect(forbidden.filter((api) => overlay.includes(api))).toEqual([]);
		expect(forbidden.filter((api) => picker.includes(api))).toEqual(['execCommand(']);
	});

	it('keeps the picker’s own menu out of the overlay’s scan', () => {
		// The menu lives in the CANVAS document and prints tokens, so un-skipped the overlay would chip its own picker.
		expect(source).toContain("var MENU_ID = 'gjs-email-field-menu'");
		expect(source).toMatch(/SKIP_SELECTOR\s*=[\s\S]{0,200}MENU_ID/);
	});

	it('measures with Range rects and paints into one identified layer', () => {
		expect(source).toContain('getClientRects');
		expect(source).toContain("var OVERLAY_ID = 'gjs-email-jinja-hl'");
		// Off must remove every trace, so the layer is looked up by id and removed, not merely hidden.
		expect(source).toContain('getElementById(OVERLAY_ID)');
		expect(source).toContain('removeChild');
	});

	it('marks the token WITHOUT redrawing it, so the copy stays legible', () => {
		// THE GREY-BLOB BUG: an overprinted label caps at 9-11px and measured 1.19:1, so no label and no monospace stack.
		const code = stripComments(source);
		for (const gone of [
			'FIELD_WASH',
			'TAG_WASH',
			'FIELD_INK',
			'TAG_INK',
			'MIN_CHIP_FONT',
			'fittedFontSize',
			'fillTracking',
			'textContent = label'
		])
			expect(code, `${gone} is back`).not.toContain(gone);

		expect(code).toContain('CHIP_FILL');
		expect(code).toContain('CHIP_RING');
		expect(code).toContain('mix-blend-mode:multiply');
	});

	it('keeps the layer out of its own stacking context, or multiply cannot reach the page', () => {
		// `mix-blend-mode` blends inside its own stacking context, and a NUMERIC z-index creates one — hence the flat grey.
		expect(source).toMatch(
			/layer\.style\.cssText = 'position:absolute;top:0;left:0;pointer-events:none;'/
		);
		expect(source).not.toMatch(/cssText = '[^']*z-index/);
	});

	it('is NEUTRAL for both kinds — no indigo', () => {
		// One quiet treatment for both kinds, pinned on the HUES so a rename cannot smuggle one back; scoped to the chip.
		const chipFn = /function chip\([\s\S]*?\n\t\}/.exec(source)?.[0] ?? '';
		const palette = /var CHIP_FILL[\s\S]*?var CHIP_RING = '[^']*';/.exec(source)?.[0] ?? '';
		expect(chipFn, 'chip() not found').not.toBe('');
		expect(palette, 'the chip palette was not found').not.toBe('');
		for (const indigo of ['#eef2ff', '#c7d2fe', '#4338ca', '129, 140, 248']) {
			expect(chipFn, `${indigo} is back in chip()`).not.toContain(indigo);
			expect(palette, `${indigo} is back in the palette`).not.toContain(indigo);
		}
	});

	it('leaves a node alone while the rich-text editor holds it', () => {
		// A repositioned overlay fights a live caret and an opaque chip would cover the characters being typed.
		expect(source).toContain('[contenteditable="true"]');
	});

	it('listens for the parent’s toggle under the name the document editor already uses', () => {
		expect(source).toContain("'gjs-jinja-hl'");
	});
});

/** THE CONDITIONAL BADGE: a real element, because a pseudo-element cannot be clicked to say WHAT is conditional. */
/** A CONDITION IS MARKED IN THE RAIL, NOT ON THE CANVAS. Every on-element treatment tried here was
 *  eclipsed by the element itself — a button's dark fill and a field chip's own grey both swallowed the
 *  ring — and a badge painted over the copy is a claim about what the recipient sees. The signal lives
 *  beside the Visibility and Repeat headings in the style rail (`inspector-sector`'s `mark`). */
describe('the canvas draws nothing for an authored condition', () => {
	const script = readFileSync(SCRIPT, 'utf8');
	const styles = readFileSync(join(REPO_ROOT, 'apps/platform/static/gjs-email-canvas.css'), 'utf8');

	it('has no badge, no popover and no glyphs left in the script', () => {
		for (const gone of [
			'conditionBadge',
			'revealCondition',
			'data-cond-badge',
			'CONDITION_D',
			'LOOP_D',
			'MARKER_INK'
		])
			expect(script, gone).not.toContain(gone);
	});

	it('reads no condition attribute at all — the overlay is only for raw Jinja now', () => {
		expect(script).not.toMatch(/querySelectorAll\('\[data-if\]/);
	});

	it('leaves no ring in the canvas stylesheet', () => {
		// The rules that painted one are gone; the file still MENTIONS the attributes in the note that
		// says why, so this asserts on the declarations rather than the words.
		expect(styles).not.toMatch(/outline-style:\s*dotted/);
		expect(styles).not.toMatch(/outline-color:\s*rgba\(194/);
	});
});
