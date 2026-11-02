/** `getCss()` re-emits every inline declaration as an `#id` rule that can never match — `avoidInlineStyle: false` puts styles ON the element and `getHtml()` carries no such id — 24 rules, 1557 bytes on the shipped document-request template; the ids also regenerate on every parse, so an untouched template diffed on every save. */
import { describe, expect, it } from 'vitest';
import { pruneOrphanCss } from './orphan-css';

const HTML =
	'<body><table style="width:100%"><tr><td style="padding:8px">x</td></tr></table></body>';

describe('pruneOrphanCss', () => {
	it('drops a rule whose id is nowhere in the document', () => {
		expect(pruneOrphanCss('#i5uaf{width:100%;}', HTML)).toBe('');
	});

	it('drops the whole generated sheet, not just the first rule', () => {
		const css = '#a{width:100%;}#b{height:600px;}#c{padding:0;}';
		expect(pruneOrphanCss(css, HTML)).toBe('');
	});

	it('KEEPS a rule that can actually match', () => {
		// Tests whether a rule CAN match, not whether it looks generated — a hand-set or future id must survive.
		const html = '<div id="masthead">x</div>';
		expect(pruneOrphanCss('#masthead{color:red;}', html)).toBe('#masthead{color:red;}');
	});

	it('keeps a multi-selector rule if ANY selector matches', () => {
		const html = '<div id="keep">x</div>';
		const css = '#gone, #keep{color:red;}';
		expect(pruneOrphanCss(css, html)).toBe(css);
	});

	it('leaves every other kind of rule alone', () => {
		// Only unmatchable ids are pruned — a class, element, at-rule or pseudo-class is left alone either way.
		const css =
			'.cta{color:red;}td{padding:0;}@media (max-width:480px){td{padding:4px;}}a:hover{color:blue;}';
		expect(pruneOrphanCss(css, HTML)).toBe(css);
	});

	it('normalises a sheet of nothing but orphans to empty', () => {
		// Whitespace left behind would read as "there is css here" to the unsaved-changes baseline.
		expect(pruneOrphanCss('#a{width:1px;}\n\n#b{width:2px;}\n', HTML)).toBe('');
	});

	it('is total on empty input', () => {
		expect(pruneOrphanCss('', HTML)).toBe('');
		expect(pruneOrphanCss('#a{width:1px;}', '')).toBe('');
	});
});

describe('a rule body containing a Jinja token', () => {
	it('does not end the rule at the token’s own closing braces', () => {
		// The first cut's `[^}]*` stopped at the token's own `}}`, leaving a broken fragment (`};border-radius:...`) instead of pruning the rule; found from a real template's output, not a fixture.
		const css =
			'#i1{background-color:{{ brand.accent }};border-radius:10px 10px 0 0;}#i2{padding:0;}';
		expect(pruneOrphanCss(css, '<div>x</div>')).toBe('');
	});

	it('still keeps such a rule when its id matches', () => {
		const css = '#bar{background-color:{{ brand.accent }};border-radius:6px;}';
		expect(pruneOrphanCss(css, '<td id="bar">x</td>')).toBe(css);
	});

	it('handles several tokens in one body', () => {
		const css = '#a{color:{{ brand.ink }};background:{{ brand.accent }};margin:0;}';
		expect(pruneOrphanCss(css, '<div>x</div>')).toBe('');
	});
});
