import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { ComponentProps } from 'svelte';
import Money from './money.svelte';

/**
 * SSR pins for Money's finish-axis numeral face. The figure reads
 * `font-family: var(--money-display)` with NO fallback and NO theme.css default: unset, the
 * failed var() substitution computes as `unset` and font-family INHERITS (a mono reference
 * column keeps mono figures); under the ledger finish the token points at var(--font-display)
 * and every figure takes the firm's display face. The class must sit on the value span itself —
 * and on the placeholder, so an empty cell lines up with its column in the same face.
 */

describe('Money (SSR)', () => {
	it('the figure carries the numeral-face knob alongside tabular-nums', () => {
		const { body } = render(Money, { props: { amount: 1240.5 } });
		expect(body).toContain('font-family:var(--money-display)');
		expect(body).toContain('tabular-nums');
		expect(body).toContain('£1,240');
	});

	it('prints the row currency, not a hardcoded pound', () => {
		const usd = render(Money, { props: { amount: 1240.5, currency: 'USD' } }).body;
		expect(usd).toMatch(/\$1,240/);
		expect(usd).not.toContain('£');
		const eur = render(Money, { props: { amount: 1240.5, currency: 'EUR' } }).body;
		expect(eur).toContain('€1,240');
	});

	it('the placeholder carries it too, so empty cells sit in the column face', () => {
		const { body } = render(Money, { props: { amount: null } });
		expect(body).toContain('font-family:var(--money-display)');
		expect(body).toContain('—');
	});
});

/**
 * Two tinted tones, because the estate carries two token families for "good/bad" and this
 * component renders under both: the adviser book paints money movements from the STATUS pair,
 * the client portal from the MONEY pair. One for everything would give one of those surfaces a
 * second green.
 */
describe('Money tones (SSR)', () => {
	const render_ = (props: ComponentProps<typeof Money>) => render(Money, { props }).body;

	it('directional paints the status pair', () => {
		expect(render_({ amount: 10, signed: true, tone: 'directional' })).toContain(
			'text-success-strong'
		);
		expect(render_({ amount: -10, signed: true, tone: 'directional' })).toContain(
			'text-destructive'
		);
	});

	it('money paints the money pair', () => {
		expect(render_({ amount: 10, signed: true, tone: 'money' })).toContain('text-positive');
		expect(render_({ amount: -10, signed: true, tone: 'money' })).toContain('text-negative');
	});

	it('neither tints zero — it is neither, and a green zero claims something happened', () => {
		for (const tone of ['directional', 'money'] as const) {
			const body = render_({ amount: 0, signed: true, tone });
			expect(body).not.toContain('text-positive');
			expect(body).not.toContain('text-success-strong');
			expect(body).toContain('text-muted-foreground'); // the untinted pence
		}
	});

	// The whole point of routing a tint through `tone` rather than `class`: the minor span is
	// inside this component, so only a tone can stop it being grey. A red total ending in grey
	// pence is what a caller-supplied colour leaves behind.
	it('the pence follow the tint instead of staying muted', () => {
		const body = render_({ amount: -874.48, signed: true, tone: 'money' });
		expect(body).toContain('opacity-70');
		expect(body).not.toContain('text-muted-foreground');
	});

	it('the sign still renders, so colour never carries the meaning alone', () => {
		expect(render_({ amount: -874.48, signed: true, tone: 'money' })).toContain('-£874');
		expect(render_({ amount: 874.48, signed: true, tone: 'money' })).toContain('+£874');
	});
});
