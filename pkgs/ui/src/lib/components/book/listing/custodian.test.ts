import { describe, expect, it } from 'vitest';
import { custodianLabel } from './custodian.js';

describe('custodianLabel', () => {
	it('prints the custodian names, and sentence-cases a code it has not met', () => {
		expect(custodianLabel('seccl')).toBe('Seccl');
		expect(custodianLabel('sei')).toBe('SEI');
		expect(custodianLabel('some_other')).toBe('Some other');
	});
});
