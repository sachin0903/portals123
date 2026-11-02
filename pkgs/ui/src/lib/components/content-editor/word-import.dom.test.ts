import { describe, expect, it } from 'vitest';
import { normaliseWordHtml } from './word-import.js';

describe('Word smart import', () => {
	it('turns a plain Word-shaped document into semantic article content', () => {
		const html = normaliseWordHtml(`
			<p>Planning for retirement</p>
			<p>A practical introduction to making confident decisions.</p>
			<h2>Your options</h2>
			<p style="font-family: Calibri" class="word-noise">Start with your objectives.</p>
			<ul><li>Review your income</li></ul>
		`);

		expect(html).toContain('class="article-shell"');
		expect(html).toContain('class="article-kicker"');
		expect(html).toContain('<h1>Planning for retirement</h1>');
		expect(html).toContain(
			'<p class="article-lede">A practical introduction to making confident decisions.</p>'
		);
		expect(html).toContain('<h2>Your options</h2>');
		expect(html).toContain('<ul><li>Review your income</li></ul>');
		expect(html).not.toContain('Calibri');
		expect(html).not.toContain('word-noise');
	});

	it('keeps an authored heading and supplies accessible image defaults', () => {
		const html = normaliseWordHtml('<h1>Tax planning</h1><img src="/chart.png"><p>Read this.</p>');

		expect(html.match(/<h1>/g)).toHaveLength(1);
		expect(html).toContain('loading="lazy"');
		expect(html).toContain('alt=""');
	});
});
