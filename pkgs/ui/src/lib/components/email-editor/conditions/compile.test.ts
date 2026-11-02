import { describe, expect, it } from 'vitest';
import { encodeChoice, encodeVisibility } from './attrs';
import {
	compileConditionals,
	fallbackGuard,
	findCloseTag,
	hasAuthoredConditions,
	readAttr,
	readOpenTag,
	stripEditorAttrs
} from './compile';
import type { Choice, Visibility } from './model';
import { BRAND_LOGO_HTML } from '../create';

/** The transform whose failure ships a broken email. Wrapped WRONG stops the whole template rendering;
 *  DROPPED renders fine and is merely missing a paragraph — hence: unreadable is left as it came in. */

const vis = (over: Partial<Visibility> = {}): Visibility => ({
	action: 'show',
	match: 'all',
	rules: [{ field: 'is_reminder', op: 'set' }],
	...over
});

const ifAttr = (v: Visibility = vis()) => `data-if="${encodeVisibility(v)}"`;
const choiceAttr = (c: Choice) => `data-choice="${encodeChoice(c)}"`;

describe('readOpenTag', () => {
	it('reads a tag and its name', () => {
		const tag = readOpenTag('<td class="x">hi</td>', 0);
		expect(tag).toMatchObject({ name: 'td', start: 0, end: 14, selfClosing: false });
		expect('<td class="x">hi</td>'.slice(0, tag!.end)).toBe('<td class="x">');
	});

	it('does not mistake a > INSIDE an attribute for the tag’s end', () => {
		// A real case: the canvas label reads `If price is “>5”`, and that lands in an attribute.
		const html = '<td data-if-label="If x is &quot;>5&quot;" class="y">hi</td>';
		const tag = readOpenTag(html, 0);
		expect(html.slice(tag!.start, tag!.end)).toBe(
			'<td data-if-label="If x is &quot;>5&quot;" class="y">'
		);
	});

	it('knows a void element closes itself', () => {
		expect(readOpenTag('<img src="a">', 0)?.selfClosing).toBe(true);
		expect(readOpenTag('<br>', 0)?.selfClosing).toBe(true);
		expect(readOpenTag('<hr/>', 0)?.selfClosing).toBe(true);
	});

	it('is null for anything that is not an opening tag', () => {
		expect(readOpenTag('</td>', 0)).toBeNull();
		expect(readOpenTag('text', 0)).toBeNull();
		expect(readOpenTag('<!-- c -->', 0)).toBeNull();
		expect(readOpenTag('<td class="x"', 0)).toBeNull();
	});
});

describe('findCloseTag', () => {
	const extent = (html: string) => {
		const open = readOpenTag(html, 0)!;
		return findCloseTag(html, open);
	};

	it('counts nesting on the tag name', () => {
		const html = '<td><td>inner</td></td>';
		expect(extent(html)).toBe(html.length);
	});

	it('is not confused by a different nested tag', () => {
		const html = '<td><span>x</span></td>';
		expect(extent(html)).toBe(html.length);
	});

	it('is null when unbalanced', () => {
		expect(extent('<td>no close')).toBeNull();
		expect(extent('<td><td>one close</td>')).toBeNull();
	});

	it('matches the WHOLE closing name, not a prefix of it', () => {
		// `</a` used to match `</abbr>`: the close side tested a prefix while the OPEN side counted
		// depth on an exact name, so nothing offset the false close and the element was truncated at
		// the first longer-named child. Both of these returned early, silently dropping the tail.
		const anchor = '<a href="#">see <abbr>ISA</abbr> now</a>';
		expect(extent(anchor)).toBe(anchor.length);
		const para = '<p>text <pre>code</pre> more</p>';
		expect(extent(para)).toBe(para.length);
		// The compensating case still works: a real sibling after the close is not swallowed.
		expect(extent('<a href="#">x</a><abbr>y</abbr>')).toBe('<a href="#">x</a>'.length);
	});
});

describe('readAttr / stripEditorAttrs', () => {
	it('reads a double-quoted attribute', () => {
		expect(readAttr('<td data-if="abc" class="x">', 'data-if')).toBe('abc');
		expect(readAttr('<td class="x">', 'data-if')).toBeNull();
	});

	it('does not match a longer attribute name by prefix', () => {
		// `data-if` must not read `data-if-label`'s value — the leading \\s and the `="` are what stop it.
		expect(readAttr('<td data-if-label="If x">', 'data-if')).toBeNull();
	});

	it('removes every editor attribute and nothing else', () => {
		const tag = `<td ${ifAttr()} data-if-label="If x" ${choiceAttr({ rule: { field: 'a', op: 'set' }, then: 'y', otherwise: 'n' })} class="keep" style="color:red">`;
		const stripped = stripEditorAttrs(tag);
		expect(stripped).toContain('class="keep"');
		expect(stripped).toContain('style="color:red"');
		expect(stripped).not.toContain('data-if');
		expect(stripped).not.toContain('data-choice');
	});
});

describe('compileConditionals — block conditions', () => {
	it('leaves html with no conditions byte-identical', () => {
		// The overwhelming majority of templates. Any change here is a change to every email.
		const html = '<table><tr><td style="padding:10px">Hello {{ first_name }}</td></tr></table>';
		expect(compileConditionals(html)).toBe(html);
	});

	it('wraps the element and strips the bookkeeping', () => {
		const html = `<td ${ifAttr()} data-if-label="If is_reminder has a value" class="x">Due soon.</td>`;
		expect(compileConditionals(html)).toBe(
			'{% if is_reminder %}<td class="x">Due soon.</td>{% endif %}'
		);
	});

	it('wraps the WHOLE element, children included', () => {
		const html = `<td ${ifAttr()}><p>a</p><p>b</p></td>`;
		expect(compileConditionals(html)).toBe(
			'{% if is_reminder %}<td><p>a</p><p>b</p></td>{% endif %}'
		);
	});

	it('emits “unless” as a negation', () => {
		const html = `<td ${ifAttr(vis({ action: 'hide' }))}>x</td>`;
		expect(compileConditionals(html)).toBe('{% if not (is_reminder) %}<td>x</td>{% endif %}');
	});

	it('nests properly', () => {
		const inner = vis({ rules: [{ field: 'due_date', op: 'set' }] });
		const html = `<td ${ifAttr()}><p ${ifAttr(inner)}>Due</p></td>`;
		expect(compileConditionals(html)).toBe(
			'{% if is_reminder %}<td>{% if due_date %}<p>Due</p>{% endif %}</td>{% endif %}'
		);
	});

	it('handles a void element with a condition', () => {
		const html = `<img ${ifAttr()} src="a.png">`;
		expect(compileConditionals(html)).toBe('{% if is_reminder %}<img src="a.png">{% endif %}');
	});

	it('leaves siblings and surrounding text untouched', () => {
		const html = `<p>before</p><td ${ifAttr()}>mid</td><p>after</p>`;
		expect(compileConditionals(html)).toBe(
			'<p>before</p>{% if is_reminder %}<td>mid</td>{% endif %}<p>after</p>'
		);
	});

	describe('refusals', () => {
		it('does NOT wrap when the condition has no usable rule, but still strips the attributes', () => {
			const html = `<td ${ifAttr(vis({ rules: [{ field: '', op: 'set' }] }))} class="x">keep me</td>`;
			expect(compileConditionals(html)).toBe('<td class="x">keep me</td>');
		});

		it('does NOT wrap an unreadable condition', () => {
			const html = '<td data-if="not-json" class="x">keep me</td>';
			expect(compileConditionals(html)).toBe('<td class="x">keep me</td>');
		});

		it('does NOT wrap an element whose close tag is missing', () => {
			const html = `<td ${ifAttr()}>never closed`;
			const out = compileConditionals(html);
			expect(out).not.toContain('{% if');
			expect(out).toContain('never closed');
		});

		it('refuses a field that is not a plain reference', () => {
			const html = `<td ${ifAttr(vis({ rules: [{ field: 'x|safe', op: 'set' }] }))}>x</td>`;
			expect(compileConditionals(html)).toBe('<td>x</td>');
		});

		/** A hand-typed `data-if` reaches this from the code view, which offers the source shape for
		 *  editing: well-formed JSON whose `rules` hold something that is not a rule. The whole
		 *  condition is unreadable then, exactly like `data-if="not-json"` — dropping only the bad
		 *  element would widen an `all` condition into one nobody authored. */
		const rawIf = (value: unknown) => `data-if="${encodeVisibility(value as Visibility)}"`;

		it('does NOT wrap when a rule is null', () => {
			const html = `<td ${rawIf({ action: 'show', match: 'all', rules: [null] })} class="x">keep me</td>`;
			expect(compileConditionals(html)).toBe('<td class="x">keep me</td>');
		});

		it('does NOT wrap when a rule’s field is not a string', () => {
			const html = `<td ${rawIf({ action: 'show', match: 'all', rules: [{ field: 1, op: 'set' }] })}>x</td>`;
			expect(compileConditionals(html)).toBe('<td>x</td>');
		});

		it('does NOT wrap when a rule’s value is not a string', () => {
			const rules = [{ field: 'a', op: 'eq', value: 1 }];
			const html = `<td ${rawIf({ action: 'show', match: 'all', rules })}>x</td>`;
			expect(compileConditionals(html)).toBe('<td>x</td>');
		});

		it('does NOT wrap when one rule of several is not a rule', () => {
			const rules = [{ field: 'a', op: 'set' }, null];
			const html = `<td ${rawIf({ action: 'show', match: 'all', rules })}>x</td>`;
			expect(compileConditionals(html)).toBe('<td>x</td>');
		});
	});
});

describe('compileConditionals — inline choices', () => {
	const choice: Choice = {
		rule: { field: 'is_reminder', op: 'set' },
		then: 'Reminder',
		otherwise: 'New task'
	};

	it('REPLACES the element with one expression', () => {
		const html = `<h1><span ${choiceAttr(choice)}>Reminder</span>: {{ task_title }}</h1>`;
		expect(compileConditionals(html)).toBe(
			"<h1>{{ 'Reminder' if is_reminder else 'New task' }}: {{ task_title }}</h1>"
		);
	});

	it('discards the preview text of the branch it was showing', () => {
		const html = `<span ${choiceAttr(choice)}>whatever was previewed</span>`;
		expect(compileConditionals(html)).not.toContain('whatever was previewed');
	});

	it('keeps the element when the choice is unreadable, so its text still ships', () => {
		const html = '<span data-choice="not-json">Reminder</span>';
		expect(compileConditionals(html)).toBe('<span>Reminder</span>');
	});

	it('keeps the element when the rule is unusable', () => {
		const bad = choiceAttr({ ...choice, rule: { field: '', op: 'set' } });
		expect(compileConditionals(`<span ${bad}>Reminder</span>`)).toBe('<span>Reminder</span>');
	});

	it('keeps the element when the rule is not a rule at all', () => {
		// The `data-if` case in its inline costume: readable JSON, a `rule` object that holds a number.
		const bad = `data-choice="${encodeChoice({ ...choice, rule: { field: 1 } } as unknown as Choice)}"`;
		expect(compileConditionals(`<span ${bad}>Reminder</span>`)).toBe('<span>Reminder</span>');
	});

	it('escapes both branches through the model', () => {
		const apostrophe: Choice = { ...choice, then: "adviser's", otherwise: 'none' };
		expect(compileConditionals(`<span ${choiceAttr(apostrophe)}>x</span>`)).toBe(
			"{{ 'adviser\\'s' if is_reminder else 'none' }}"
		);
	});

	it('works inside a conditional block', () => {
		const html = `<td ${ifAttr()}><span ${choiceAttr(choice)}>Reminder</span></td>`;
		expect(compileConditionals(html)).toBe(
			"{% if is_reminder %}<td>{{ 'Reminder' if is_reminder else 'New task' }}</td>{% endif %}"
		);
	});
});

describe('compileConditionals — hostile and odd input', () => {
	it('survives a stray < in text', () => {
		expect(compileConditionals('<p>5 < 6</p>')).toBe('<p>5 < 6</p>');
	});

	it('leaves an html comment alone', () => {
		expect(compileConditionals('<!-- note --><p>x</p>')).toBe('<!-- note --><p>x</p>');
	});

	it('leaves existing hand-typed Jinja alone', () => {
		// Stored templates are full of hand-typed Jinja; the compile must not touch it or double-wrap it.
		const html = '<p>{% if x %}a{% else %}b{% endif %}</p>';
		expect(compileConditionals(html)).toBe(html);
	});

	it('answers empty for empty', () => {
		expect(compileConditionals('')).toBe('');
	});
});

describe('hasAuthoredConditions', () => {
	it('is the cheap skip for a template with none', () => {
		expect(hasAuthoredConditions('<p>plain</p>')).toBe(false);
		expect(hasAuthoredConditions(`<td ${ifAttr()}>x</td>`)).toBe(true);
		expect(
			hasAuthoredConditions(
				`<span ${choiceAttr({ rule: { field: 'a', op: 'set' }, then: 'x', otherwise: 'y' })}>x</span>`
			)
		).toBe(true);
	});

	it('is not fooled by the label attribute alone', () => {
		expect(hasAuthoredConditions('<td data-if-label="If x">y</td>')).toBe(false);
	});
});

/**
 * AN IMAGE'S FALLBACK IS GUARDED ON ITS OWN SOURCE.
 *
 * The shipped masthead stores `field: "brand.logo_url"` because that is what its `src` is. That field
 * was used verbatim, so changing the Source to a hosted URL still compiled `{% if brand.logo_url %}`
 * — and for a firm with no stored logo the recipient got the WORDMARK while the author's image was
 * silently dropped. It looked right on the canvas, which is the one failure this editor exists to
 * prevent. The guard is derived now, so the branch and the image cannot disagree.
 */
describe('fallbackGuard', () => {
	it('guards on the token the source names', () => {
		expect(fallbackGuard('{{ brand.logo_url }}', 'stored')).toBe('brand.logo_url');
		expect(fallbackGuard('  {{  brand.icon_url  }}  ', 'stored')).toBe('brand.icon_url');
	});

	it('ignores a filter on the way', () => {
		expect(fallbackGuard("{{ brand.logo_url | default('') }}", 'stored')).toBe('brand.logo_url');
	});

	it('answers NO GUARD for a literal source, because the fallback is unreachable', () => {
		for (const src of ['https://cdn.example.com/logo.png', '/local.png', 'data:image/png;base64,x'])
			expect(fallbackGuard(src, 'brand.logo_url'), src).toBeNull();
	});

	it('falls back to the stored field only when there is no src to read', () => {
		expect(fallbackGuard(null, 'brand.logo_url')).toBe('brand.logo_url');
	});
});

describe('compiling the masthead', () => {
	const fallback = encodeURIComponent(
		JSON.stringify({ field: 'brand.logo_url', text: '{{ brand.name }}', tag: 'div', style: 'x' })
	);
	// THE ARM MARKER IS REQUIRED: the wordmark belongs to the brand logo, so a `data-fallback` on
	// anything else compiles as itself. `unmarked` is that case.
	const img = (src: string) =>
		`<img data-brand-arm="logo" src="${src}" data-fallback="${fallback}" alt="a"/>`;
	const unmarked = (src: string) => `<img src="${src}" data-fallback="${fallback}" alt="a"/>`;

	it('KEEPS the wordmark fallback for the brand logo — the shipped behaviour', () => {
		const out = compileConditionals(img('{{ brand.logo_url }}'));
		expect(out).toContain('{% if brand.logo_url %}');
		expect(out).toContain('{% else %}<div style="x">{{ brand.name }}</div>{% endif %}');
	});

	it('drops the branch once the author points it at a real image', () => {
		// Without this the author's own logo never arrives: the branch asks about a value they did not
		// change, and a firm with no stored logo gets the wordmark instead of the image they chose.
		const out = compileConditionals(img('https://cdn.example.com/logo.png'));
		expect(out).not.toContain('{% if');
		expect(out).not.toContain('{{ brand.name }}');
		expect(out).toContain('src="https://cdn.example.com/logo.png"');
	});

	it('gives NO wordmark to an image that is not the brand logo', () => {
		// A duplicated masthead, or a stray copy of the attribute, used to compile its own
		// `{% else %}wordmark{% endif %}` and put the firm's name where no wordmark belongs.
		const out = compileConditionals(unmarked('{{ brand.logo_url }}'));
		expect(out).not.toContain('{% if');
		expect(out).not.toContain('{{ brand.name }}');
		expect(out).toContain('src="{{ brand.logo_url }}"');
	});

	it('follows a DIFFERENT token rather than the stored one', () => {
		const out = compileConditionals(img('{{ brand.icon_url }}'));
		expect(out).toContain('{% if brand.icon_url %}');
		expect(out).not.toContain('{% if brand.logo_url %}');
	});
});

describe('a condition authored ON a choice or a fallback', () => {
	/** `data-if` is independent of what else the element is. It was applied on the LAST compile branch
	 *  only, and `stripEditorAttrs` removes the attribute on the way out — so a condition on a
	 *  conditional text or on the masthead was silently dropped and the element always shipped. That is
	 *  the failure mode worth a test: the email renders perfectly and shows what should be hidden. */
	const withIf = (html: string) => html.replace('<img ', `<img ${ifAttr()} `);
	const choice: Choice = {
		rule: { field: 'is_reminder', op: 'set' },
		then: 'Reminder',
		otherwise: 'Notice'
	};

	it('wraps a conditional text in its condition', () => {
		const compiled = compileConditionals(`<span ${choiceAttr(choice)} ${ifAttr()}>Reminder</span>`);
		expect(compiled).toContain('{% if is_reminder %}');
		expect(compiled).toContain('{% endif %}');
		// The choice's own inline expression is still there, inside the gate.
		expect(compiled).toContain('Reminder');
		expect(compiled).not.toContain('data-if');
		expect(compiled).not.toContain('data-choice');
	});

	it('wraps the masthead in its condition, outside the fallback if/else', () => {
		const compiled = compileConditionals(`<td>${withIf(BRAND_LOGO_HTML)}</td>`);
		// Two conditions now: the authored one and the fallback arm.
		expect(compiled.match(/\{% endif %\}/g)).toHaveLength(2);
		expect(compiled).toContain('{% if is_reminder %}');
		expect(compiled).toContain('{% if brand.logo_url %}');
		// The authored gate is OUTSIDE: it decides whether the masthead appears at all.
		expect(compiled.indexOf('{% if is_reminder %}')).toBeLessThan(
			compiled.indexOf('{% if brand.logo_url %}')
		);
		expect(compiled).not.toContain('data-if');
		expect(compiled).not.toContain('data-fallback');
	});

	it('wraps a fallback element that has no brand arm, where there is no if/else to nest in', () => {
		// No `data-brand-arm`, so the guard is null and the element compiles as itself — the branch that
		// emitted `kept` bare.
		const noArm = withIf(BRAND_LOGO_HTML).replace(/ data-brand-arm="[^"]*"/, '');
		const compiled = compileConditionals(`<td>${noArm}</td>`);
		expect(compiled).toContain('{% if is_reminder %}');
		expect(compiled.match(/\{% endif %\}/g)).toHaveLength(1);
		// No wordmark: the arm marker is what identifies a masthead.
		expect(compiled).not.toContain('{% else %}');
	});

	it('still refuses to gate an unbalanced tag', () => {
		// A missing close tag means no known end, and a misplaced `{% endif %}` stops the WHOLE template.
		const compiled = compileConditionals(`<div ${ifAttr()}>never closed`);
		expect(compiled).not.toContain('{% if');
		expect(compiled).not.toContain('{% endif %}');
	});
});
