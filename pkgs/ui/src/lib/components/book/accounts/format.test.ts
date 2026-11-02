import { describe, expect, it } from 'vitest';
import { transactionReference } from './format.js';

describe('transactionReference', () => {
	it('strips the source_system prefix up to the first colon', () => {
		expect(transactionReference('seccl:AB12345')).toBe('AB12345');
	});

	it('keeps everything after the first colon, even if it contains further colons', () => {
		expect(transactionReference('sei:2026-08-19:AB12345')).toBe('2026-08-19:AB12345');
	});

	it('returns a value with no colon unchanged', () => {
		expect(transactionReference('AB12345')).toBe('AB12345');
	});

	it('shows a Graphene-originated row as its public id', () => {
		expect(transactionReference('graphene:gxn:instruction:7GmVwQQKjMcA')).toBe('7GmVwQQKjMcA');
		expect(transactionReference('gxn:contribution:7GmVwQQKjMcA')).toBe('7GmVwQQKjMcA');
		expect(transactionReference('graphene:gxn:transfer:7GmVwQQKjMcA')).toBe('7GmVwQQKjMcA');
	});
});
