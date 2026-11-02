import { describe, it, expect } from 'vitest';
import { lintJinjaTemplate } from './jinja-table-lint';

describe('lintJinjaTemplate', () => {
	it('passes a well-formed data-table template', () => {
		const tmpl =
			'<table>{% for row in holdings %}<tr><td>{{ row.fund }}</td><td>{{ row.value }}</td></tr>' +
			'{% endfor %}</table>';
		expect(lintJinjaTemplate(tmpl)).toEqual([]);
	});

	it('flags an unclosed {% for %} loop', () => {
		const issues = lintJinjaTemplate('{% for row in holdings %}{{ row.fund }}');
		expect(issues.some((i) => i.includes('endfor'))).toBe(true);
	});

	it('flags unbalanced output braces', () => {
		const issues = lintJinjaTemplate('<td>{{ row.fund }</td>');
		expect(issues.some((i) => i.includes('output braces'))).toBe(true);
	});

	it('flags an unclosed {% if %} (endfor is not counted as if/endif)', () => {
		const issues = lintJinjaTemplate('{% if row.active %}{{ row.fund }}{% endfor %}');
		expect(issues.some((i) => i.includes('condition'))).toBe(true);
	});

	it('does not miscount endfor as for, or endif as if', () => {
		// Balanced: one for/endfor, one if/endif — the "end" tags must not inflate the open counts.
		const tmpl = '{% for r in d %}{% if r.x %}{{ r.x }}{% endif %}{% endfor %}';
		expect(lintJinjaTemplate(tmpl)).toEqual([]);
	});

	it('is empty for plain HTML or an empty template', () => {
		expect(lintJinjaTemplate('')).toEqual([]);
		expect(lintJinjaTemplate('<p>Just text.</p>')).toEqual([]);
	});
});
