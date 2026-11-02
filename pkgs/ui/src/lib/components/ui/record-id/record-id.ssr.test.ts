import { render } from 'svelte/server';
import { describe, expect, it } from 'vitest';
import RecordId from './record-id.svelte';

describe('RecordId', () => {
	it('keeps the label readable and mutes only the identifier', () => {
		const html = render(RecordId, { props: { value: 'abc123' } }).body;

		expect(html).toContain('ID:');
		expect(html).toContain('text-muted-foreground');
		expect(html).toContain('font-mono');
		expect(html).toContain('>abc123<');
	});

	it('caps a long identifier to its column and keeps the label', () => {
		// The contributions queue puts this under `items-start`, which sizes a child to its text.
		// `truncate` then has nothing to clip, and the id paints into the next column. The label
		// must not be the part that disappears.
		const reference = 'demo-ten_d558803fc7a54b89-1-1-tx-1';
		const html = render(RecordId, {
			props: { label: 'Transaction', value: reference }
		}).body;

		expect(html).toContain('shrink-0');
		expect(html).toContain('Transaction:');
		expect(html).toContain(`>${reference}<`);
		expect(html).toContain('min-w-0');
		expect(html).toContain('max-w-full');
		expect(html).toContain('truncate');
		// Same affordance as TruncatedText: the trigger is in the markup, the tooltip itself is
		// not, until layout says the line is actually clipping.
		expect(html).toContain('data-slot="tooltip-trigger"');
		expect(html).not.toContain('data-slot="tooltip-content"');
	});

	it('can sit inside a sentence', () => {
		// Review detail prints "Case: RV-4 · submitted by …". `inline` has to win over the flex
		// row, and the value must not be a block that drops the rest of the sentence.
		const html = render(RecordId, {
			props: { label: 'Case', value: 'RV-4', class: 'inline' }
		}).body;

		expect(html).toContain('Case:');
		expect(html).toContain('>RV-4<');
		expect(html).toMatch(/class="[^"]*\binline\b/);
		expect(html).not.toMatch(/<span[^>]*\bblock\b[^>]*>RV-4/);
	});

	it('renders nothing without an identifier', () => {
		expect(render(RecordId, { props: { value: null } }).body).not.toContain('ID:');
	});
});
