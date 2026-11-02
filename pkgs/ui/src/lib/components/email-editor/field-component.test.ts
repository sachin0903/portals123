import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
	FIELD_ATTR,
	FIELD_FALLBACK_ATTR,
	FIELD_FILTER_ATTR,
	fieldSpanHtml,
	readFieldSpan,
	registerEmailField
} from './create';
import { EDITOR_ATTRS } from './conditions/attrs';
import { fieldToken, parseFieldToken } from './field-filters';
import { stripEditorAttrs } from './conditions/compile';

// A merge field is a component, not plain text, so it can be selected: `{{ amount }}` renders `1234.5` where the firm means £1,234.50, and applying the currency filter needs something to apply it to.
describe('fieldSpanHtml', () => {
	it('writes the token and the structured value together', () => {
		const html = fieldSpanHtml('amount', 'currency');
		expect(html).toContain('{{ amount | currency }}');
		expect(html).toContain(`${FIELD_ATTR}="amount"`);
		expect(html).toContain(`${FIELD_FILTER_ATTR}="currency"`);
		expect(html).toContain('data-gjs-type="email-field"');
	});

	it('makes the token atomic, so a brace cannot be deleted', () => {
		// `editable: false` alone only stops an RTE opening ON the span — `contenteditable="false"` is what stops the caret walking THROUGH it from the paragraph's own RTE, or `{{ first_nam }}` renders as nothing and `{{ first_name` as literal braces in a client's email.
		expect(fieldSpanHtml('first_name')).toContain('contenteditable="false"');
	});

	it('never ships the editing attribute', () => {
		expect(EDITOR_ATTRS).toContain('contenteditable');
		expect(stripEditorAttrs('<span contenteditable="false" data-field="x">')).not.toContain(
			'contenteditable'
		);
	});

	it('omits the filter attribute entirely when there is none', () => {
		// Not an empty attribute — `data-field-filter=""` and "no filter" would be two spellings of one state, which is how a panel starts disagreeing with itself.
		const html = fieldSpanHtml('first_name');
		expect(html).toContain('{{ first_name }}');
		expect(html).not.toContain(FIELD_FILTER_ATTR);
	});

	it('answers empty for no field, so nothing inserts a span around nothing', () => {
		expect(fieldSpanHtml('')).toBe('');
		expect(fieldSpanHtml('   ', 'currency')).toBe('');
	});
});

describe('readFieldSpan', () => {
	const fake = (attrs: Record<string, string>, content = '') => ({
		getAttributes: () => attrs,
		get: (key: string) => (key === 'content' ? content : undefined)
	});

	it('reads the attributes when they are there', () => {
		expect(
			readFieldSpan(fake({ [FIELD_ATTR]: 'amount', [FIELD_FILTER_ATTR]: 'currency' }))
		).toEqual({ field: 'amount', filter: 'currency', fallback: '' });
	});

	it('reads a field with no filter as having none', () => {
		expect(readFieldSpan(fake({ [FIELD_ATTR]: 'first_name' }))).toEqual({
			field: 'first_name',
			filter: '',
			fallback: ''
		});
	});

	it('falls back to the TEXT for a span that carries no attributes', () => {
		// A pasted or hand-written span carries no attributes; refusing to describe it would leave the author looking at a field the panel pretends not to see.
		expect(readFieldSpan(fake({}, '{{ amount | currency }}'))).toEqual({
			field: 'amount',
			filter: 'currency',
			fallback: ''
		});
	});

	it('reads the fallback, and does not trim it', () => {
		// A single-space fallback is a real answer — it keeps a sentence's spacing where a name would go. Blank is what means "no fallback".
		expect(readFieldSpan(fake({ [FIELD_ATTR]: 'first_name', [FIELD_FALLBACK_ATTR]: ' ' }))).toEqual(
			{ field: 'first_name', filter: '', fallback: ' ' }
		);
		expect(
			readFieldSpan(fake({ [FIELD_ATTR]: 'first_name', [FIELD_FALLBACK_ATTR]: 'there' }))
		).toEqual({ field: 'first_name', filter: '', fallback: 'there' });
	});

	it('is null for anything that is not a field', () => {
		expect(readFieldSpan(null)).toBeNull();
		expect(readFieldSpan(fake({}, 'Write your message here.'))).toBeNull();
		expect(readFieldSpan(fake({}, '{% if x %}'))).toBeNull();
	});
});

describe('the field attributes never reach a recipient', () => {
	it('is on the strip list, so the compiled email carries the token alone', () => {
		// The TOKEN is what Jinja evaluates; these two attributes are editor bookkeeping only, stripped before send like the condition attributes already are.
		expect(EDITOR_ATTRS).toContain(FIELD_ATTR);
		expect(EDITOR_ATTRS).toContain(FIELD_FILTER_ATTR);
		const stripped = stripEditorAttrs(
			`<span data-gjs-type="email-field" ${FIELD_ATTR}="amount" ${FIELD_FILTER_ATTR}="currency">`
		);
		expect(stripped).not.toContain(FIELD_ATTR);
		expect(stripped).not.toContain(FIELD_FILTER_ATTR);
	});
});

describe('registration', () => {
	const source = readFileSync(join(import.meta.dirname, 'create.ts'), 'utf8');

	it('is registered beside the other two custom types', () => {
		// An unregistered type resolves to a plain span — selectable AND editable — so an author can type over half a token and produce Jinja that renders as literal braces in an email.
		expect(source).toMatch(/registerEmailField\(editor\);/);
	});

	it('is not editable, and is removable but no longer draggable', () => {
		const block =
			source.split('export function registerEmailField')[1]?.split('\nexport ')[0] ?? '';
		expect(block).toContain('editable: false');
		expect(block).toContain('droppable: false');
		expect(block).toContain('removable: true');
		// `draggable` was true, on the reasoning that a word in a sentence should be movable; reversed because an inline span's drag resolves through GrapesJS's sorter geometry (verified live that Resend's equivalent object doesn't offer it either) — a field moves by editing the copy around it.
		expect(block).toContain('draggable: false');
	});
});

describe('a field is its own OBJECT, not editable text', () => {
	// A field typed via the `{{` picker, or dropped into a paragraph, is committed by GrapesJS's RTE, whose re-parse REWRITES `data-gjs-type` to `text` and strips `contenteditable` — measured live as `{type: 'text', editable: true, contenteditable: null}` — so the span became deletable text and `{{ first_nam }}` / `{{ first_name` would silently break in a client's email.
	// Resend's equivalent object behaves the same way: double-click selects the chip, and typing while it's selected replaces the whole thing.
	type Spec = {
		isComponent: (el: { getAttribute: (k: string) => string | null }) => unknown;
		model: { defaults: Record<string, unknown> };
	};
	const registered = (): Spec | undefined => {
		let spec: Spec | undefined;
		registerEmailField({
			DomComponents: { addType: (_id: string, s: Spec) => (spec = s) }
		});
		return spec;
	};

	it('claims a span by `data-field`, which survives the RTE re-parse', () => {
		const isComponent = registered()?.isComponent;
		expect(isComponent).toBeTypeOf('function');
		if (!isComponent) return;
		const el = (attrs: Record<string, string>) => ({
			getAttribute: (k: string) => attrs[k] ?? null
		});
		expect(isComponent(el({ 'data-field': 'first_name' }))).toEqual({ type: 'email-field' });
		expect(isComponent(el({ 'data-gjs-type': 'email-field' }))).toEqual({ type: 'email-field' });
		expect(isComponent(el({}))).toBeUndefined();
		expect(isComponent(el({ 'data-gjs-type': 'text' }))).toBeUndefined();
	});

	it('is not editable, and re-asserts the attribute the parse strips', () => {
		const defaults = registered()?.model?.defaults ?? {};
		// The model flag stops GrapesJS opening an RTE on the span itself.
		expect(defaults.editable).toBe(false);
		// Declared again here because the RTE's re-parse (above) strips the `contenteditable` attribute `fieldSpanHtml` wrote it with.
		expect(defaults.attributes).toEqual({ contenteditable: 'false' });
	});

	it('is NOT draggable', () => {
		// Same reversal as the registration check above — an inline span's drag resolves through GrapesJS's sorter geometry, which Resend's equivalent object doesn't expose either.
		expect(registered()?.model?.defaults?.draggable).toBe(false);
	});
});

/** A token carrying a fallback must land WITH it. Found in the browser: the preview said
 *  `{{ account_value | fallback('your balance') }}` and the inserted span said `{{ account_value }}`,
 *  because `fieldMarkup` rebuilt the span from the field and filter only. Latent until a declared
 *  variable became the first thing to offer a token with a fallback in it. */
describe('inserting a token that carries a fallback', () => {
	const source = readFileSync(new URL('./email-editor.svelte', import.meta.url), 'utf8').replace(
		/<!--[\s\S]*?-->/g,
		''
	);

	it('rebuilds the span from all THREE parts, not two', () => {
		expect(source).toMatch(/fieldSpanHtml\(parsed\.field, parsed\.filter, parsed\.fallback\)/);
	});

	it('round-trips through the real builders', () => {
		const token = fieldToken('account_value', '', 'your balance');
		const parsed = parseFieldToken(token);
		expect(parsed?.fallback).toBe('your balance');
		const span = fieldSpanHtml(parsed!.field, parsed!.filter, parsed!.fallback);
		expect(span).toContain('data-field-fallback="your balance"');
		// The token TEXT is the Jinja itself; only an attribute value would be entity-escaped.
		expect(span).toContain(token);
	});
});

describe('a fallback is safe in the element BODY, not only in the attribute', () => {
	// THE BUG. `fieldSpanHtml` escaped the three data-field-* attributes and then wrote the Jinja
	// token — which embeds the author's free-text fallback verbatim — into the span's body raw.
	// `sel.components(token)` PARSES that string, so `<none>` became a bogus element: the chip read
	// `fallback('')`, the saved html carried a stray tag, the sent email never rendered the fallback,
	// and the panel (reading the correctly-escaped attribute) still reported it as set.
	it('escapes markup a fallback contains', () => {
		const html = fieldSpanHtml('first_name', '', '<none>');
		expect(html).toContain('&lt;none&gt;');
		expect(html).not.toContain('<none>');
	});

	it('cannot be closed out of its own span', () => {
		const html = fieldSpanHtml('first_name', '', '</span><img src=x onerror=alert(1)>');
		// One opening span and one closing span, and no img anywhere.
		expect(html.match(/<span/g)?.length).toBe(1);
		expect(html.match(/<\/span>/g)?.length).toBe(1);
		expect(html).not.toContain('<img');
	});

	it('leaves the quotes Jinja needs alone', () => {
		// escapeText deliberately does NOT touch `'` — those are the delimiters of `fallback('…')`, and
		// escaping them would stop the template compiling at all.
		const html = fieldSpanHtml('first_name', '', 'there');
		expect(html).toContain("fallback('there')");
	});

	it('escapes an ampersand once, so the email draws one', () => {
		const html = fieldSpanHtml('firm_name', '', 'Ridley & Co');
		expect(html).toContain('Ridley &amp; Co');
		expect(html).not.toContain('Ridley & Co<');
	});
});
