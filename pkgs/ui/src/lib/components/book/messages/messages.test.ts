import { describe, expect, it } from 'vitest';
import { staffSenderLabel } from './messages';

describe('staff sender attribution', () => {
	it('names a foreign operations sender as Graphene support without losing their name', () => {
		expect(staffSenderLabel({ sender_name: 'Olivia Operator', sender_is_support: true })).toBe(
			'Graphene support · Olivia Operator'
		);
	});

	it('leaves a firm staff sender unchanged', () => {
		expect(staffSenderLabel({ sender_name: 'Alex Adviser', sender_is_support: false })).toBe(
			'Alex Adviser'
		);
	});
});
