import { describe, expect, it } from 'vitest';
import { birthLine, clientKind, completedAge, isChild } from './client-kind.js';

const today = new Date('2026-09-28T12:00:00Z');

describe('clientKind', () => {
	it('counts completed years, not the calendar difference', () => {
		expect(completedAge('2014-03-03', today)).toBe(12);
		// Birthday later this year: still the age from last birthday.
		expect(completedAge('2008-12-25', today)).toBe(17);
		expect(completedAge('2008-09-28', today)).toBe(18);
		expect(completedAge(null, today)).toBeNull();
		expect(completedAge('not a date', today)).toBeNull();
	});

	it('marks an individual under 18 as a child and nobody else', () => {
		expect(isChild('2014-03-03', today)).toBe(true);
		expect(isChild('2008-09-28', today)).toBe(false);
		expect(isChild(null, today)).toBe(false);
		expect(clientKind({ type: 'individual', dob: '2014-03-03' }, today)).toBe('child');
		expect(clientKind({ type: 'individual', dob: '1981-02-01' }, today)).toBe('individual');
		expect(clientKind({ type: 'individual' }, today)).toBe('individual');
		// An organisation has no birthday to be under 18 by.
		expect(clientKind({ type: 'organisation', dob: '2020-01-01' }, today)).toBe('organisation');
	});

	it('states the date of birth with the age', () => {
		expect(birthLine('2014-03-03', today)).toBe('Born 3 Mar 2014 (12)');
		expect(birthLine(null, today)).toBeNull();
	});
});
