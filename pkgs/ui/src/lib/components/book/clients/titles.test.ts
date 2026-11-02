import { describe, expect, it } from 'vitest';
import { PERSON_TITLES, titleItems } from './types.js';

describe('titleItems', () => {
	it('offers the titles all three custodians share', () => {
		expect(PERSON_TITLES).toEqual(['Mr', 'Mrs', 'Miss', 'Ms', 'Dr', 'Prof']);
	});

	it('keeps a stored title the shared list does not name', () => {
		const items = titleItems('Baroness');
		expect(items.at(-1)).toEqual({ value: 'Baroness', label: 'Baroness' });
		expect(items).toHaveLength(PERSON_TITLES.length + 1);
	});

	it('does not offer a bare custodian code as a choice', () => {
		expect(titleItems('1').map((item) => item.value)).toEqual([...PERSON_TITLES]);
		expect(titleItems('Mr').map((item) => item.value)).toEqual([...PERSON_TITLES]);
		expect(titleItems(null).map((item) => item.value)).toEqual([...PERSON_TITLES]);
	});
});
