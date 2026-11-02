import { describe, expect, it } from 'vitest';
import { initials, personInitials } from './initials';

describe('initials', () => {
	it('takes up to two initials from a name', () => {
		expect(initials('Morgan Lloyd')).toBe('ML');
		expect(initials('Graphene')).toBe('G');
		expect(initials('Mr Max Oakden')).toBe('MM');
	});

	it('collapses extra whitespace between words', () => {
		expect(initials('  Morgan   Lloyd  ')).toBe('ML');
	});

	it('falls back for an empty or blank name, defaulting to "?"', () => {
		expect(initials('')).toBe('?');
		expect(initials('   ')).toBe('?');
		expect(initials('', 'G')).toBe('G');
		expect(initials('   ', '·')).toBe('·');
	});

	it('never strips a real word, even one that reads like a title', () => {
		expect(initials('Dr Martens')).toBe('DM');
	});
});

describe('personInitials', () => {
	it('drops a leading honorific', () => {
		expect(personInitials('Mr Max Oakden')).toBe('MO');
		expect(personInitials('Dr Freddie Salcombe')).toBe('FS');
		expect(personInitials('Miss Sophie Halloway')).toBe('SH');
		expect(personInitials('Mrs. Oscar Kingsley')).toBe('OK');
	});

	it('leaves a name with no honorific alone', () => {
		expect(personInitials('Andrew Gaunt')).toBe('AG');
		expect(personInitials('Futura')).toBe('F');
	});

	it('falls back to the honorific itself rather than nothing, when it is the whole name', () => {
		expect(personInitials('Dr', '·')).toBe('D');
	});

	it('falls back for an empty name', () => {
		expect(personInitials('', '·')).toBe('·');
	});
});
