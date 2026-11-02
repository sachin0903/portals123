import { describe, expect, it } from 'vitest';
import { KIND_BY_ROUTE, parseAmount, pendingLabel } from './money-movement';

describe('parseAmount', () => {
	it('reads the money field as it posts: plain, grouped, or with the symbol', () => {
		expect(parseAmount('500')).toBe(500);
		expect(parseAmount('1,500.00')).toBe(1500);
		expect(parseAmount('£2,500')).toBe(2500);
		expect(parseAmount(' 12.5 ')).toBe(12.5);
	});

	it('refuses what is not a positive amount with at most two decimal places', () => {
		for (const raw of ['', '0', '0.00', '-5', '1.234', 'abc', '1e3', '5,0,0', null, 7, undefined]) {
			expect(parseAmount(raw)).toBeNull();
		}
	});
});

describe('the route segment ↔ the API kind', () => {
	it('keeps `deposit` in the URL and `withdraw` for the other, nothing else', () => {
		expect(KIND_BY_ROUTE.deposit).toBe('deposit');
		expect(KIND_BY_ROUTE.withdraw).toBe('withdrawal');
		expect(KIND_BY_ROUTE.withdrawal).toBeUndefined();
		expect(KIND_BY_ROUTE.DEPOSIT).toBeUndefined();
	});
});

describe('pendingLabel', () => {
	it('names the two directions the way the client reads them', () => {
		expect(pendingLabel('expected_in')).toBe('Payment in');
		expect(pendingLabel('withdrawal')).toBe('Withdrawal');
		expect(pendingLabel('model_withdrawal')).toBe('Withdrawal');
	});
});
