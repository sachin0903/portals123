import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import MoneyInput from './money-input.svelte';

/**
 * Pins the two decisions that make this a MONEY field rather than a number box: the control is
 * never type="number" (a scroll wheel over a focused number input silently changes the amount),
 * and the currency's symbol is shown from the code without the caller having to know it.
 */
describe('MoneyInput (SSR)', () => {
	it('is a decimal text field, not a number spinner', () => {
		const { body } = render(MoneyInput, { props: { value: 12.5 } });
		expect(body).toContain('inputmode="decimal"');
		expect(body).toContain('type="text"');
		expect(body).not.toContain('type="number"');
	});

	it('renders the value at the currency precision', () => {
		const { body } = render(MoneyInput, { props: { value: 12.5 } });
		expect(body).toContain('value="12.50"');
	});

	it('derives the symbol from the currency code', () => {
		expect(render(MoneyInput, { props: { value: 1, currency: 'GBP' } }).body).toContain('£');
		expect(render(MoneyInput, { props: { value: 1, currency: 'EUR' } }).body).toContain('€');
		// Unknown code falls back to the code itself rather than to a wrong symbol.
		expect(render(MoneyInput, { props: { value: 1, currency: 'ZWL' } }).body).toContain('ZWL');
		// An explicit symbol (e.g. loaded from ref.currencies) wins.
		expect(
			render(MoneyInput, { props: { value: 1, currency: 'ZWL', symbol: 'Z$' } }).body
		).toContain('Z$');
	});

	it('shows nothing for a null amount — not zero', () => {
		// A blank fee and a fee of zero are different facts, so an empty value must not render 0.00.
		const { body } = render(MoneyInput, { props: { value: null } });
		expect(body).toContain('value=""');
	});

	// The wrapper is the field's box: a portal sizing fields for touch sizes it by this slot, and
	// the inner input — which can never be the full 44 inside the wrapper's border — is waived
	// from the touch audit rather than sized past the box around it.
	it('names its wrapper as the field and waives the inner input', () => {
		const { body } = render(MoneyInput, { props: { value: 1 } });
		expect(body).toContain('data-slot="money-input"');
		expect(body).toMatch(/<input[^>]*data-tap-ok/);
	});

	it('respects a zero-decimal currency', () => {
		const { body } = render(MoneyInput, { props: { value: 1200, currency: 'JPY', precision: 0 } });
		expect(body).toContain('value="1,200"');
		expect(body).toContain('¥');
	});
});
