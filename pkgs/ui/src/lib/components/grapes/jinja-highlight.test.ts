/**
 * The Jinja run-finder the code view and the canvas overlay both colour with.
 *
 * ONLY the matcher's own behaviour is pinned here. The email branch's copy of this file also pinned
 * PARITY with `apps/platform/static/gjs-email-jinja-hl.js` — the matcher re-implemented in the script
 * GrapesJS injects into its iframe, which cannot be imported and so is extracted and compared. That
 * belongs beside the editor that ships the script, the way `style-wiring.test.ts` does: this package
 * has no canvas of its own, and a shared test naming one app's static asset fails for every other
 * caller. The document editor ships `grapesjs-jinja-vars.js` and wants the same guarantee — worth its
 * own test there, since the two scripts are separate re-implementations.
 */
import { describe, expect, it } from 'vitest';
import { jinjaRuns } from './jinja-highlight';

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
