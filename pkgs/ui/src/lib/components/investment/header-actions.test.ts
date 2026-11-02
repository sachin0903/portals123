import { describe, expect, it } from 'vitest';
import { shownMoneyButtons } from './header-actions.svelte';

describe('shownMoneyButtons', () => {
	it('keeps the pair when nothing is switched off', () => {
		expect(shownMoneyButtons('deposit_withdraw', [])).toEqual({ payment: true, withdraw: true });
	});

	it('omits the button whose action is switched off', () => {
		expect(shownMoneyButtons('deposit_withdraw', ['transaction.payment'])).toEqual({
			payment: false,
			withdraw: true
		});
		expect(shownMoneyButtons('deposit_withdraw', ['transaction.withdrawal'])).toEqual({
			payment: true,
			withdraw: false
		});
		expect(
			shownMoneyButtons('deposit_withdraw', ['transaction.payment', 'transaction.withdrawal'])
		).toEqual({ payment: false, withdraw: false });
	});

	it('leaves a stored contact or none with no money buttons', () => {
		expect(shownMoneyButtons('contact', ['transaction.payment'])).toEqual({
			payment: false,
			withdraw: false
		});
		expect(shownMoneyButtons('none')).toEqual({ payment: false, withdraw: false });
	});
});
