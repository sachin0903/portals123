import { describe, expect, it } from 'vitest';
import { paymentInWording } from './funding';

describe('paymentInWording', () => {
	it('keeps contribution for a pension or a JISA', () => {
		expect(paymentInWording('SIPP')).toEqual({ row: 'Contributions', fallback: 'Contribution' });
		expect(paymentInWording('jisa').row).toBe('Contributions');
		expect(paymentInWording('TPPENSION').fallback).toBe('Contribution');
	});

	it('says payment in for every other wrapper', () => {
		expect(paymentInWording('GIA')).toEqual({ row: 'Payments in', fallback: 'Payment in' });
		expect(paymentInWording('ISA').row).toBe('Payments in');
		expect(paymentInWording(null).fallback).toBe('Payment in');
	});
});
